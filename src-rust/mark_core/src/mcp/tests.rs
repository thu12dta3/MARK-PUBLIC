use super::protocol::{parse_json_rpc_message, McpError, MAX_PAYLOAD_BYTES};
use super::security::{normalize_path, McpSecurityPolicy};
use std::path::{Path, PathBuf};

#[test]
fn test_reject_oversized_payload() {
    // Simula injeção de payload JSON gigante de 5MB (limite estrito é 4MB)
    let oversized_len = MAX_PAYLOAD_BYTES + 1024 * 1024; // 5 MB
    let huge_data = vec![b'a'; oversized_len];

    // O parser DEVE rejeitar antes de qualquer alocação excessiva com McpError::PayloadTooLarge
    let result = parse_json_rpc_message(&huge_data, MAX_PAYLOAD_BYTES);
    match result {
        Err(McpError::PayloadTooLarge { size, limit }) => {
            assert_eq!(size, oversized_len);
            assert_eq!(limit, MAX_PAYLOAD_BYTES);
        }
        other => {
            panic!(
                "FALHA DE SEGURANÇA (RED): Payload gigante não foi rejeitado pelo limite de buffer! Resultado: {:?}",
                other
            );
        }
    }
}

#[test]
fn test_strict_path_normalization_blocks_traversal() {
    let base_dir = Path::new("/home/user/workspace");

    // Tentativas maliciosas de Path Traversal
    let malicious_paths = vec![
        "../../etc/shadow",
        "foo/../../../etc/passwd",
        "/etc/shadow",
        "nested/../../../../root/.ssh/id_rsa",
        "test/\0/malicious",
        "....//....//etc/passwd",
        "./../../sensitive/data.db",
        "/home/user/workspace/../secret",
    ];

    for bad_path in malicious_paths {
        let result = normalize_path(base_dir, bad_path);
        // Na implementação segura, DEVE retornar Err(McpError::PathTraversal(_))
        match result {
            Err(McpError::PathTraversal(_)) => {
                // Protegido com sucesso contra path traversal
            }
            Ok(escaped_path) => {
                panic!(
                    "FALHA DE SEGURANÇA: Path traversal permitiu acesso indevido! Entrada: '{}', Resolvido para: '{:?}'",
                    bad_path, escaped_path
                );
            }
            Err(other) => {
                panic!(
                    "Esperava McpError::PathTraversal para '{}', obteve {:?}",
                    bad_path, other
                );
            }
        }
    }

    // Caminho legítimo dentro do workspace deve ser normalizado e aprovado
    let valid_path = "subfolder/document.txt";
    let normalized = normalize_path(base_dir, valid_path).expect("Caminho válido deve ser aceito");
    assert_eq!(normalized, base_dir.join("subfolder/document.txt"));
}

#[test]
fn test_enforce_read_only_capability() {
    let mut policy = McpSecurityPolicy::new();
    let base_dir = PathBuf::from("/home/user/workspace");

    // Registra ferramenta estritamente ReadOnly
    policy.register_read_only_tool("read_file", vec![base_dir.clone()]);

    // Tentativa de executar mutação através de uma ferramenta ReadOnly
    let result = policy.validate_tool_call("read_file", true, Some("file.txt"), &base_dir);

    match result {
        Err(McpError::PermissionDenied(_)) => {
            // Isolamento de privilégios funcionou perfeitamente
        }
        Ok(_) => {
            panic!(
                "FALHA DE SEGURANÇA: Violação de privilégios! Ação de escrita/mutação foi permitida em ferramenta ReadOnly!"
            );
        }
        Err(other) => {
            panic!("Esperava McpError::PermissionDenied, obteve {:?}", other);
        }
    }
}

#[test]
fn test_unregistered_tool_execution_denied() {
    let mut policy = McpSecurityPolicy::new();
    let base_dir = PathBuf::from("/home/user/workspace");

    // Allowlist contém apenas "read_file"
    policy.register_read_only_tool("read_file", vec![base_dir.clone()]);

    // Tentativa de invocar ferramenta não registrada / maliciosa
    let result = policy.validate_tool_call("execute_arbitrary_shell", false, None, &base_dir);

    match result {
        Err(McpError::UnauthorizedTool(tool)) => {
            assert_eq!(tool, "execute_arbitrary_shell");
        }
        Ok(_) => {
            panic!(
                "FALHA DE SEGURANÇA: Ferramenta não registrada foi aceita sem validação da allowlist!"
            );
        }
        Err(other) => {
            panic!("Esperava McpError::UnauthorizedTool, obteve {:?}", other);
        }
    }
}

#[test]
fn test_json_rpc_parser_fuzzing() {
    // Casos de fuzzing: entradas truncadas, binárias, bytes nulos, recursão excessiva e tipos inválidos
    let fuzz_cases: Vec<&[u8]> = vec![
        b"",
        b"\x00\x01\x02\xff\xfe",
        b"{\"jsonrpc\": \"2.0\", \"id\": ",
        b"{\"jsonrpc\": \"1.0\", \"id\": 1, \"result\": null}",
        b"{\"jsonrpc\": \"2.0\", \"id\": \"string_id_not_int\"}",
        b"{\"jsonrpc\": \"2.0\", \"id\": 1, \"result\": [{{{{{{{{",
        b"{\"jsonrpc\": \"2.0\", \"id\": 1, \"error\": \"not_an_object\"}",
        b"{\"jsonrpc\": null, \"id\": null}",
        b"\r\n\r\n\x00\x00",
        b"{\"jsonrpc\": \"2.0\", \"id\": 9999999999999999999999999999999999999999999999999999}",
    ];

    for case in fuzz_cases {
        // O parser nunca deve entrar em pânico sob fuzzing, deve retornar Err
        let res = parse_json_rpc_message(case, MAX_PAYLOAD_BYTES);
        assert!(
            res.is_err(),
            "Entrada malformada deveria falhar: {:?}",
            String::from_utf8_lossy(case)
        );
    }
}

#[test]
fn test_physical_memory_separation_of_capabilities() {
    let mut policy = McpSecurityPolicy::new();
    let base_dir = PathBuf::from("/home/user/workspace");

    policy.register_read_only_tool("inspect_ast", vec![base_dir.clone()]);
    policy.register_write_mutate_tool("apply_patch", vec![base_dir.clone()]);

    // Valida que os endereços de memória das scopes de Leitura e Escrita são fisicamente distintos
    let read_ptr = &policy.read_only_scope as *const _;
    let write_ptr = &policy.write_mutate_scope as *const _;
    assert_ne!(
        read_ptr as usize, write_ptr as usize,
        "Escopos de memória de permissões devem ser fisicamente separados!"
    );

    // Acesso ReadOnly a inspect_ast deve suceder
    assert!(policy
        .validate_tool_call("inspect_ast", false, None, &base_dir)
        .is_ok());

    // Acesso Write a inspect_ast deve falhar com PermissionDenied
    assert!(matches!(
        policy.validate_tool_call("inspect_ast", true, None, &base_dir),
        Err(McpError::PermissionDenied(_))
    ));

    // Acesso Write a apply_patch deve suceder
    assert!(policy
        .validate_tool_call("apply_patch", true, None, &base_dir)
        .is_ok());

    // Acesso Read a apply_patch deve falhar (uma ferramenta de mutação não pode ser chamada em contexto read-only desavisado)
    assert!(matches!(
        policy.validate_tool_call("apply_patch", false, None, &base_dir),
        Err(McpError::PermissionDenied(_))
    ));
}

#[test]
fn test_out_of_order_message_handling() {
    let policy = McpSecurityPolicy::new();
    let base_dir = PathBuf::from("/home/user/workspace");
    let mut client = super::client::McpClient::new(base_dir, policy);

    // Espera ID 10
    let expected_id = 10;

    // Servidor responde com ID 99 (fora de ordem ou fraudulento)
    let out_of_order_payload = br#"{"jsonrpc": "2.0", "id": 99, "result": {"status": "ok"}}"#;

    let res = client.process_raw_response(expected_id, out_of_order_payload);
    match res {
        Err(McpError::OutOfOrderMessage { expected, received }) => {
            assert_eq!(expected, 10);
            assert_eq!(received, 99);
        }
        other => {
            panic!(
                "Esperava McpError::OutOfOrderMessage, mas obteve: {:?}",
                other
            );
        }
    }

    // Servidor responde com o ID esperado 10
    let valid_payload = br#"{"jsonrpc": "2.0", "id": 10, "result": {"status": "ok"}}"#;
    let res = client.process_raw_response(expected_id, valid_payload);
    assert!(res.is_ok(), "Mensagem com ID correto deve ser aceita");
}
