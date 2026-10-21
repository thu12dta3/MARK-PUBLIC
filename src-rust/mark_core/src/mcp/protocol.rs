use serde::{Deserialize, Serialize};
use std::fmt;

pub const MAX_PAYLOAD_BYTES: usize = 4 * 1024 * 1024; // 4 MB

#[derive(Debug, PartialEq, Eq, Clone)]
pub enum McpError {
    PayloadTooLarge { size: usize, limit: usize },
    PathTraversal(String),
    UnauthorizedTool(String),
    PermissionDenied(String),
    OutOfOrderMessage { expected: u64, received: u64 },
    ProtocolError(String),
    ProcessError(String),
    ParseError(String),
}

impl fmt::Display for McpError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            McpError::PayloadTooLarge { size, limit } => {
                write!(
                    f,
                    "Payload too large: {} bytes (limit: {} bytes)",
                    size, limit
                )
            }
            McpError::PathTraversal(path) => {
                write!(f, "Path traversal attack detected: {}", path)
            }
            McpError::UnauthorizedTool(tool) => {
                write!(f, "Tool not in allowlist: {}", tool)
            }
            McpError::PermissionDenied(reason) => {
                write!(f, "Permission denied: {}", reason)
            }
            McpError::OutOfOrderMessage { expected, received } => {
                write!(
                    f,
                    "Out-of-order JSON-RPC message: expected id {}, received {}",
                    expected, received
                )
            }
            McpError::ProtocolError(err) => write!(f, "Protocol error: {}", err),
            McpError::ProcessError(err) => write!(f, "Process error: {}", err),
            McpError::ParseError(err) => write!(f, "Parse error: {}", err),
        }
    }
}

impl std::error::Error for McpError {}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub id: u64,
    pub method: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    pub id: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcErrorObject>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct JsonRpcErrorObject {
    pub code: i64,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
}

/// Parser JSON-RPC 2.0 seguro, imune a DoS e fuzzing
pub fn parse_json_rpc_message(data: &[u8], max_bytes: usize) -> Result<JsonRpcResponse, McpError> {
    // 1. Limite rígido de tamanho de buffer (prevenção contra OOM)
    if data.len() > max_bytes {
        return Err(McpError::PayloadTooLarge {
            size: data.len(),
            limit: max_bytes,
        });
    }

    if data.is_empty() {
        return Err(McpError::ParseError("Empty payload".to_string()));
    }

    // 2. Rejeição de bytes nulos em payloads de controle
    if data.contains(&0) {
        return Err(McpError::ParseError(
            "Null byte in JSON-RPC payload".to_string(),
        ));
    }

    // 3. Desserialização estrita
    let resp: JsonRpcResponse =
        serde_json::from_slice(data).map_err(|e| McpError::ParseError(e.to_string()))?;

    // 4. Verificação estrita da especificação JSON-RPC 2.0
    if resp.jsonrpc != "2.0" {
        return Err(McpError::ProtocolError(format!(
            "Invalid jsonrpc version: expected '2.0', got '{}'",
            resp.jsonrpc
        )));
    }

    // 5. O payload de resposta deve conter ou 'result' ou 'error'
    if resp.result.is_none() && resp.error.is_none() {
        return Err(McpError::ProtocolError(
            "JSON-RPC response must contain either 'result' or 'error'".to_string(),
        ));
    }

    Ok(resp)
}
