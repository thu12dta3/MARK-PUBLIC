use super::client::McpClient;
use super::protocol::McpError;
use super::security::McpSecurityPolicy;
use pyo3::exceptions::{PyPermissionError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::runtime::Runtime;

fn map_mcp_error_to_py(err: McpError) -> PyErr {
    match err {
        McpError::PayloadTooLarge { size, limit } => PyValueError::new_err(format!(
            "PayloadTooLarge: {} bytes exceeds limit of {} bytes",
            size, limit
        )),
        McpError::PathTraversal(path) => {
            PyPermissionError::new_err(format!("PathTraversalDetected: '{}'", path))
        }
        McpError::UnauthorizedTool(tool) => {
            PyPermissionError::new_err(format!("UnauthorizedTool: '{}' not in allowlist", tool))
        }
        McpError::PermissionDenied(reason) => {
            PyPermissionError::new_err(format!("PermissionDenied: {}", reason))
        }
        McpError::OutOfOrderMessage { expected, received } => PyRuntimeError::new_err(format!(
            "OutOfOrderMessage: expected id {}, received {}",
            expected, received
        )),
        McpError::ProtocolError(msg) => PyRuntimeError::new_err(format!("ProtocolError: {}", msg)),
        McpError::ProcessError(msg) => PyRuntimeError::new_err(format!("ProcessError: {}", msg)),
        McpError::ParseError(msg) => PyValueError::new_err(format!("ParseError: {}", msg)),
    }
}

#[pyclass]
pub struct McpBridge {
    client: McpClient,
    runtime: Arc<Runtime>,
}

#[pymethods]
impl McpBridge {
    #[new]
    pub fn new(base_dir: String) -> PyResult<Self> {
        let base_path = PathBuf::from(base_dir);
        let policy = McpSecurityPolicy::new();
        let client = McpClient::new(base_path, policy);
        let runtime = Runtime::new().map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to create tokio runtime: {}", e))
        })?;

        Ok(Self {
            client,
            runtime: Arc::new(runtime),
        })
    }

    /// Registra ferramenta na política de segurança com escopo de permissão em memória
    pub fn register_tool(
        &mut self,
        tool: String,
        capability: String,
        allowed_paths: Option<Vec<String>>,
    ) -> PyResult<()> {
        let paths: Vec<PathBuf> = allowed_paths
            .unwrap_or_default()
            .into_iter()
            .map(PathBuf::from)
            .collect();

        match capability.to_lowercase().as_str() {
            "readonly" | "read_only" => {
                self.client.policy.register_read_only_tool(&tool, paths);
                Ok(())
            }
            "writemutate" | "write_mutate" | "write" => {
                self.client.policy.register_write_mutate_tool(&tool, paths);
                Ok(())
            }
            other => Err(PyValueError::new_err(format!(
                "Capacidade inválida: '{}'. Esperado 'ReadOnly' ou 'WriteMutate'.",
                other
            ))),
        }
    }

    /// Inicia o subprocesso do servidor MCP conectado via stdio
    pub fn start(&mut self, command: String, args: Option<Vec<String>>) -> PyResult<()> {
        let _guard = self.runtime.enter();
        let cmd_args = args.unwrap_or_default();
        self.client
            .start(&command, &cmd_args)
            .map_err(map_mcp_error_to_py)
    }

    /// Inicializa a sessão MCP com o servidor
    pub fn initialize(&mut self, py: Python) -> PyResult<String> {
        let res = py.allow_threads(|| {
            self.runtime
                .block_on(async { self.client.initialize().await })
        });

        match res {
            Ok(val) => serde_json::to_string(&val)
                .map_err(|e| PyRuntimeError::new_err(format!("Serialization error: {}", e))),
            Err(e) => Err(map_mcp_error_to_py(e)),
        }
    }

    /// Lista as ferramentas permitidas pela allowlist de segurança
    pub fn list_tools(&self) -> PyResult<Vec<String>> {
        Ok(self.client.list_tools())
    }

    /// Executa uma ferramenta no servidor MCP com validação de limites e isolamento
    pub fn call_tool(
        &mut self,
        py: Python,
        tool_name: String,
        arguments_json: String,
        is_mutation: bool,
    ) -> PyResult<String> {
        let arguments: serde_json::Value = serde_json::from_str(&arguments_json)
            .map_err(|e| PyValueError::new_err(format!("Invalid arguments JSON: {}", e)))?;

        let res = py.allow_threads(|| {
            self.runtime.block_on(async {
                self.client
                    .call_tool(&tool_name, arguments, is_mutation)
                    .await
            })
        });

        match res {
            Ok(val) => serde_json::to_string(&val)
                .map_err(|e| PyRuntimeError::new_err(format!("Serialization error: {}", e))),
            Err(e) => Err(map_mcp_error_to_py(e)),
        }
    }

    /// Valida um caminho diretamente (para testes e inspeção defensiva via Python)
    pub fn normalize_and_validate_path(&self, user_path: String) -> PyResult<String> {
        let res = super::security::normalize_path(&self.client.base_dir, &user_path)
            .map_err(map_mcp_error_to_py)?;
        Ok(res.to_string_lossy().to_string())
    }

    /// Simula processamento de resposta para teste de protocolo e limites sem subprocesso
    pub fn simulate_process_response(
        &mut self,
        expected_id: u64,
        response_bytes: &[u8],
    ) -> PyResult<String> {
        let res = self
            .client
            .process_raw_response(expected_id, response_bytes)
            .map_err(map_mcp_error_to_py)?;
        serde_json::to_string(&res)
            .map_err(|e| PyRuntimeError::new_err(format!("Serialization error: {}", e)))
    }
}
