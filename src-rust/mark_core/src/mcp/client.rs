use super::protocol::{parse_json_rpc_message, JsonRpcRequest, McpError, MAX_PAYLOAD_BYTES};
use super::security::McpSecurityPolicy;
use std::path::PathBuf;
use std::process::Stdio;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};

pub struct McpClient {
    pub base_dir: PathBuf,
    pub policy: McpSecurityPolicy,
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    reader: Option<BufReader<ChildStdout>>,
    next_request_id: u64,
}

impl McpClient {
    pub fn new(base_dir: PathBuf, policy: McpSecurityPolicy) -> Self {
        Self {
            base_dir,
            policy,
            child: None,
            stdin: None,
            reader: None,
            next_request_id: 1,
        }
    }

    /// Inicia o processo filho do servidor MCP via stdio
    pub fn start(&mut self, command: &str, args: &[String]) -> Result<(), McpError> {
        let mut cmd = Command::new(command);
        cmd.args(args)
            .current_dir(&self.base_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = cmd.spawn().map_err(|e| {
            McpError::ProcessError(format!("Failed to spawn MCP server '{}': {}", command, e))
        })?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| McpError::ProcessError("Failed to capture child stdin".to_string()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| McpError::ProcessError("Failed to capture child stdout".to_string()))?;

        self.stdin = Some(stdin);
        self.reader = Some(BufReader::new(stdout));
        self.child = Some(child);

        Ok(())
    }

    /// Envia a requisição JSON-RPC de inicialização do protocolo MCP
    pub async fn initialize(&mut self) -> Result<serde_json::Value, McpError> {
        let params = serde_json::json!({
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": { "listChanged": false }
            },
            "clientInfo": {
                "name": "mark-mcp-native-rust",
                "version": "0.1.0"
            }
        });

        self.send_request("initialize", Some(params)).await
    }

    /// Lista ferramentas permitidas pela política de segurança
    pub fn list_tools(&self) -> Vec<String> {
        let mut tools: Vec<String> = self
            .policy
            .read_only_scope
            .allowed_tools
            .iter()
            .cloned()
            .collect();
        for w in &self.policy.write_mutate_scope.allowed_tools {
            if !tools.contains(w) {
                tools.push(w.clone());
            }
        }
        tools.sort();
        tools
    }

    /// Executa uma ferramenta com verificação rígida de isolamento e limites de payload
    pub async fn call_tool(
        &mut self,
        tool_name: &str,
        mut arguments: serde_json::Value,
        is_mutation: bool,
    ) -> Result<serde_json::Value, McpError> {
        // 1. Localiza argumentos de caminho para validação e normalização estrita
        let mut path_arg: Option<String> = None;
        if let Some(obj) = arguments.as_object() {
            for key in &["path", "file_path", "filepath", "target_path", "target_dir"] {
                if let Some(val) = obj.get(*key).and_then(|v| v.as_str()) {
                    path_arg = Some(val.to_string());
                    break;
                }
            }
        }

        // 2. Validação prévia de intenção contra política de segurança de memória
        let validated_path = self.policy.validate_tool_call(
            tool_name,
            is_mutation,
            path_arg.as_deref(),
            &self.base_dir,
        )?;

        // 3. Se um caminho foi validado e normalizado, substitui no JSON de argumentos
        if let Some(norm_path) = validated_path {
            if let Some(obj) = arguments.as_object_mut() {
                for key in &["path", "file_path", "filepath", "target_path", "target_dir"] {
                    if obj.contains_key(*key) {
                        obj.insert(
                            key.to_string(),
                            serde_json::Value::String(norm_path.to_string_lossy().to_string()),
                        );
                        break;
                    }
                }
            }
        }

        // 4. Monta requisição JSON-RPC 2.0 tools/call
        let params = serde_json::json!({
            "name": tool_name,
            "arguments": arguments
        });

        self.send_request("tools/call", Some(params)).await
    }

    /// Envia requisição JSON-RPC e lê resposta com limites de buffer (AsyncReadExt::take)
    pub async fn send_request(
        &mut self,
        method: &str,
        params: Option<serde_json::Value>,
    ) -> Result<serde_json::Value, McpError> {
        let req_id = self.next_request_id;
        self.next_request_id += 1;

        let req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: req_id,
            method: method.to_string(),
            params,
        };

        let mut payload = serde_json::to_vec(&req)
            .map_err(|e| McpError::ProtocolError(format!("Failed to serialize request: {}", e)))?;
        payload.push(b'\n');

        let stdin = self.stdin.as_mut().ok_or_else(|| {
            McpError::ProcessError("MCP server process stdin not available".to_string())
        })?;

        stdin
            .write_all(&payload)
            .await
            .map_err(|e| McpError::ProcessError(format!("Failed to write to MCP server: {}", e)))?;
        stdin
            .flush()
            .await
            .map_err(|e| McpError::ProcessError(format!("Failed to flush stdin: {}", e)))?;

        // 5. Leitura da resposta usando AsyncReadExt::take para prevenir OOM
        let reader = self.reader.as_mut().ok_or_else(|| {
            McpError::ProcessError("MCP server process stdout not available".to_string())
        })?;

        let mut line_buf = Vec::new();
        // Usamos take((MAX_PAYLOAD_BYTES as u64) + 1) para limitar a leitura a 4MB + 1 byte
        let mut limited_reader = reader.take((MAX_PAYLOAD_BYTES as u64) + 1);
        let bytes_read = limited_reader
            .read_until(b'\n', &mut line_buf)
            .await
            .map_err(|e| McpError::ProcessError(format!("Error reading child stdout: {}", e)))?;

        if bytes_read > MAX_PAYLOAD_BYTES {
            return Err(McpError::PayloadTooLarge {
                size: bytes_read,
                limit: MAX_PAYLOAD_BYTES,
            });
        }

        if bytes_read == 0 {
            return Err(McpError::ProcessError(
                "MCP server stdout closed unexpectedly".to_string(),
            ));
        }

        while line_buf.ends_with(b"\n") || line_buf.ends_with(b"\r") {
            line_buf.pop();
        }

        // 6. Parser resistente e verificação de limite
        let response = parse_json_rpc_message(&line_buf, MAX_PAYLOAD_BYTES)?;

        // 7. Proteção contra mensagens fora de ordem
        if response.id != req_id {
            return Err(McpError::OutOfOrderMessage {
                expected: req_id,
                received: response.id,
            });
        }

        // 8. Tratamento de erro retornado pelo servidor MCP
        if let Some(err) = response.error {
            return Err(McpError::ProtocolError(format!(
                "MCP Server Error {}: {}",
                err.code, err.message
            )));
        }

        Ok(response.result.unwrap_or(serde_json::Value::Null))
    }

    /// Processa uma resposta bruta fornecida (usado para testes sem spawn de processo)
    pub fn process_raw_response(
        &mut self,
        expected_id: u64,
        raw_data: &[u8],
    ) -> Result<serde_json::Value, McpError> {
        let response = parse_json_rpc_message(raw_data, MAX_PAYLOAD_BYTES)?;

        if response.id != expected_id {
            return Err(McpError::OutOfOrderMessage {
                expected: expected_id,
                received: response.id,
            });
        }

        if let Some(err) = response.error {
            return Err(McpError::ProtocolError(format!(
                "MCP Server Error {}: {}",
                err.code, err.message
            )));
        }

        Ok(response.result.unwrap_or(serde_json::Value::Null))
    }
}

impl Drop for McpClient {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.start_kill();
        }
    }
}
