use crate::mcp::protocol::McpError;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ToolCapability {
    ReadOnly,
    WriteMutate,
}

#[derive(Debug, Clone, Default)]
pub struct ReadOnlyScope {
    pub allowed_tools: HashSet<String>,
    pub allowed_paths: Vec<PathBuf>,
}

#[derive(Debug, Clone, Default)]
pub struct WriteMutateScope {
    pub allowed_tools: HashSet<String>,
    pub allowed_paths: Vec<PathBuf>,
}

/// Isolamento Real no nível de memória:
/// `read_only_scope` e `write_mutate_scope` residem em estruturas e alocações de memória distintas,
/// prevenindo escalada de privilégios ou mutações inadvertidas.
#[derive(Debug, Clone, Default)]
pub struct McpSecurityPolicy {
    pub read_only_scope: ReadOnlyScope,
    pub write_mutate_scope: WriteMutateScope,
}

impl McpSecurityPolicy {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register_read_only_tool(&mut self, tool: &str, paths: Vec<PathBuf>) {
        self.read_only_scope.allowed_tools.insert(tool.to_string());
        for p in paths {
            self.read_only_scope.allowed_paths.push(p);
        }
    }

    pub fn register_write_mutate_tool(&mut self, tool: &str, paths: Vec<PathBuf>) {
        self.write_mutate_scope
            .allowed_tools
            .insert(tool.to_string());
        for p in paths {
            self.write_mutate_scope.allowed_paths.push(p);
        }
    }

    /// Validação estrita de intenção e capacidades antes do despacho
    pub fn validate_tool_call(
        &self,
        tool: &str,
        is_mutation_requested: bool,
        path_arg: Option<&str>,
        base_dir: &Path,
    ) -> Result<Option<PathBuf>, McpError> {
        let is_in_read = self.read_only_scope.allowed_tools.contains(tool);
        let is_in_write = self.write_mutate_scope.allowed_tools.contains(tool);

        // 1. Verificação de Allowlist Estrita
        if !is_in_read && !is_in_write {
            return Err(McpError::UnauthorizedTool(tool.to_string()));
        }

        // 2. Separação Física e Lógica de Privilégios (ReadOnly vs WriteMutate)
        if is_mutation_requested {
            if is_in_read && !is_in_write {
                return Err(McpError::PermissionDenied(format!(
                    "Ferramenta '{}' é estritamente ReadOnly. Mutação negada.",
                    tool
                )));
            }
            if !is_in_write {
                return Err(McpError::PermissionDenied(format!(
                    "Operação de mutação não autorizada para a ferramenta '{}'",
                    tool
                )));
            }
        } else {
            // Contexto ReadOnly
            if is_in_write && !is_in_read {
                return Err(McpError::PermissionDenied(format!(
                    "Ferramenta mutadora '{}' não pode ser invocada em contexto somente leitura.",
                    tool
                )));
            }
        }

        // 3. Normalização e Validação Estrita de Caminho
        if let Some(raw_path) = path_arg {
            let normalized = normalize_path(base_dir, raw_path)?;

            // Valida se o caminho está contido dentro dos caminhos permitidos do escopo correspondente
            let allowed_paths = if is_mutation_requested {
                &self.write_mutate_scope.allowed_paths
            } else {
                &self.read_only_scope.allowed_paths
            };

            if !allowed_paths.is_empty() {
                let inside_allowed = allowed_paths.iter().any(|allowed| {
                    if let Ok(canon_allowed) = normalize_path(base_dir, &allowed.to_string_lossy())
                    {
                        normalized.starts_with(&canon_allowed)
                    } else {
                        normalized.starts_with(allowed)
                    }
                });

                if !inside_allowed {
                    return Err(McpError::PermissionDenied(format!(
                        "Caminho '{:?}' fora dos diretórios autorizados para a ferramenta '{}'",
                        normalized, tool
                    )));
                }
            }

            Ok(Some(normalized))
        } else {
            Ok(None)
        }
    }
}

/// Normalização estrita de caminho com bloqueio de Path Traversal absoluto e relativo
pub fn normalize_path(base_dir: &Path, user_path: &str) -> Result<PathBuf, McpError> {
    // 1. Rejeição de bytes nulos e caracteres perigosos
    if user_path.contains('\0') {
        return Err(McpError::PathTraversal(user_path.to_string()));
    }

    let p = Path::new(user_path);

    // 2. Tratamento para caminhos absolutos
    if p.is_absolute() {
        let mut normalized = PathBuf::from("/");
        for comp in p.components() {
            match comp {
                Component::Prefix(_) | Component::RootDir => {}
                Component::CurDir => {}
                Component::ParentDir => {
                    normalized.pop();
                }
                Component::Normal(c) => {
                    let s = c.to_string_lossy();
                    if s.contains("..") {
                        return Err(McpError::PathTraversal(user_path.to_string()));
                    }
                    normalized.push(c);
                }
            }
        }

        if !normalized.starts_with(base_dir) {
            return Err(McpError::PathTraversal(user_path.to_string()));
        }
        return Ok(normalized);
    }

    // 3. Tratamento para caminhos relativos: ancorados no base_dir
    let mut normalized = base_dir.to_path_buf();
    for comp in p.components() {
        match comp {
            Component::Prefix(_) | Component::RootDir => {}
            Component::CurDir => {}
            Component::ParentDir => {
                if normalized == base_dir || !normalized.starts_with(base_dir) {
                    return Err(McpError::PathTraversal(user_path.to_string()));
                }
                normalized.pop();
                if !normalized.starts_with(base_dir) {
                    return Err(McpError::PathTraversal(user_path.to_string()));
                }
            }
            Component::Normal(c) => {
                let s = c.to_string_lossy();
                if s.contains("..") {
                    return Err(McpError::PathTraversal(user_path.to_string()));
                }
                normalized.push(c);
            }
        }
    }

    if !normalized.starts_with(base_dir) {
        return Err(McpError::PathTraversal(user_path.to_string()));
    }

    Ok(normalized)
}
