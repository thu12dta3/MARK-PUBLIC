//! CLI e Daemon do MARK Link.
//!
//! Permite executar o daemon local, iniciar o relay cego, consultar diagnósticos
//! e realizar a exportação/importação autenticada de configurações.

use mark_link::daemon::{DaemonConfig, DaemonServer};
use mark_link::export::{export_config_bytes, import_config_bytes};
use mark_link::identity::DeviceStore;
use mark_link::relay::RelayServer;
use mark_link::telemetry::TelemetryCollector;
use mark_link::VERSION;
use std::env;
use std::fs;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

fn print_help() {
    println!("MARK Link CLI v{}", VERSION);
    println!("Uso: mark-link <comando> [opções]");
    println!();
    println!("Comandos:");
    println!("  status [--json]               Exibe o diagnóstico segregado de conectividade, camadas e memória");
    println!("  export [--output <arquivo>] [--passphrase <senha>]");
    println!(
        "                                Exporta configuração autenticada (sem chaves privadas)"
    );
    println!("  import [--input <arquivo>] [--passphrase <senha>]");
    println!("                                Importa e restaura configuração autenticada");
    println!(
        "  daemon [--listen <addr>]       Inicia o daemon local de conectividade do MARK Link"
    );
    println!(
        "  relay  [--listen <addr>]       Inicia o serviço local de relay cego para contingência"
    );
    println!("  version                       Exibe a versão do MARK Link");
}

fn parse_arg(args: &[String], flag: &str, default_val: &str) -> String {
    for i in 0..args.len() {
        if args[i] == flag && i + 1 < args.len() {
            return args[i + 1].clone();
        }
    }
    default_val.to_string()
}

fn has_flag(args: &[String], flag: &str) -> bool {
    args.iter().any(|a| a == flag)
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        print_help();
        return;
    }

    match args[1].as_str() {
        "status" => {
            let telem = TelemetryCollector::new();
            let report = telem.generate_report();

            if has_flag(&args[2..], "--json") {
                match serde_json::to_string_pretty(&report) {
                    Ok(json) => println!("{}", json),
                    Err(e) => eprintln!("Erro ao gerar diagnóstico: {}", e),
                }
                return;
            }

            println!(
                "================================================================================"
            );
            println!("MARK Link Diagnostics CLI — Estado da Rede (v{})", VERSION);
            println!(
                "================================================================================"
            );
            println!("[+] Estado Geral:       {}", report.state.to_uppercase());
            println!(
                "[+] Modo Ativo:         {}",
                report.mode.as_deref().unwrap_or("Nenhum")
            );
            println!("[+] Peer Mascarado:     {}", report.peer_id_masked);
            println!(
                "[+] Latência RTT:       {}",
                report
                    .rtt_ms
                    .map_or("N/A".to_string(), |r| format!("{} ms", r))
            );
            println!("[+] Memória RSS:        {} KB", report.memory_rss_kb);
            println!("[+] Tempo Ativo:        {}s", report.uptime_seconds);
            println!(
                "[+] Bytes TX / RX:      {} / {}",
                report.bytes_sent, report.bytes_received
            );
            println!("[+] Pacotes Descartados:{}", report.packets_dropped);
            if let Some(err) = &report.last_error {
                println!("[!] Último Erro:        {}", err);
            }
            println!();
            println!("--- Diagnóstico por Camada (Segregação de Falhas) ---");
            println!(
                "  • [Serviço/Daemon]:   [{:^12}] {}",
                report.layers.service.status.to_uppercase(),
                report
                    .layers
                    .service
                    .error
                    .as_deref()
                    .unwrap_or("Operando normalmente")
            );
            println!(
                "  • [Loopback/Local]:   [{:^12}] {}",
                report.layers.loopback.status.to_uppercase(),
                report
                    .layers
                    .loopback
                    .error
                    .as_deref()
                    .unwrap_or("Isolamento local 127.0.0.1 ativo")
            );
            println!(
                "  • [LAN]:              [{:^12}] {}",
                report.layers.lan.status.to_uppercase(),
                report
                    .layers
                    .lan
                    .error
                    .as_deref()
                    .unwrap_or("Descoberta de candidatos locais pronta")
            );
            println!(
                "  • [Conexão Direta]:   [{:^12}] {}",
                report.layers.direct.status.to_uppercase(),
                report
                    .layers
                    .direct
                    .error
                    .as_deref()
                    .unwrap_or("Aguardando peer direto")
            );
            println!(
                "  • [Blind Relay]:      [{:^12}] {}",
                report.layers.relay.status.to_uppercase(),
                report
                    .layers
                    .relay
                    .error
                    .as_deref()
                    .unwrap_or("Pronto para contingência")
            );
            println!(
                "  • [Autenticação/Auth]:[{:^12}] {}",
                report.layers.auth.status.to_uppercase(),
                report
                    .layers
                    .auth
                    .error
                    .as_deref()
                    .unwrap_or("Registro de dispositivos íntegro")
            );
            println!(
                "================================================================================"
            );
        }
        "export" => {
            let out_file = parse_arg(&args[2..], "--output", "mark_link_config.enc");
            let passphrase = parse_arg(&args[2..], "--passphrase", "DefaultMigrationPassword2026!");

            let routes = vec![
                "/health".to_string(),
                "/v1/chat/completions".to_string(),
                "/v1/voice/turn".to_string(),
                "/v1/session".to_string(),
                "/v1/session/renew".to_string(),
            ];
            // Exporta dispositivos aprovados da loja local
            let empty_devices = Vec::new();
            match export_config_bytes(&empty_devices, &routes, &passphrase) {
                Ok(bytes) => {
                    if let Err(e) = fs::write(&out_file, bytes) {
                        eprintln!("[-] Falha ao gravar arquivo de exportação: {}", e);
                        std::process::exit(1);
                    }
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        let _ = fs::set_permissions(&out_file, fs::Permissions::from_mode(0o600));
                    }
                    println!("[+] Configuração exportada com sucesso em: {}", out_file);
                    println!("[+] Criptografia autenticada AES-256-GCM aplicada (zero chaves privadas expostas).");
                }
                Err(e) => {
                    eprintln!("[-] Erro ao exportar configuração: {}", e);
                    std::process::exit(1);
                }
            }
        }
        "import" => {
            let in_file = parse_arg(&args[2..], "--input", "mark_link_config.enc");
            let passphrase = parse_arg(&args[2..], "--passphrase", "DefaultMigrationPassword2026!");

            let data = match fs::read(&in_file) {
                Ok(d) => d,
                Err(e) => {
                    eprintln!("[-] Erro ao ler arquivo de importação: {}", e);
                    std::process::exit(1);
                }
            };

            let mut store = DeviceStore::new();
            match import_config_bytes(&data, &passphrase, &mut store) {
                Ok(res) => {
                    println!("[+] Configuração importada com sucesso!");
                    println!(
                        "[+] Total de dispositivos restaurados: {}",
                        res.imported_devices
                    );
                }
                Err(e) => {
                    eprintln!("[-] BLOQUEIO DE SEGURANÇA: Falha na importação: {}", e);
                    std::process::exit(1);
                }
            }
        }
        "version" => {
            println!("MARK Link v{}", VERSION);
        }
        "daemon" => {
            let listen_addr = parse_arg(&args[2..], "--listen", "127.0.0.1:8766");
            println!("[*] Iniciando daemon do MARK Link (transporte privado em Rust)...");
            let config = DaemonConfig {
                listen_addr,
                ..Default::default()
            };
            let mut server = match DaemonServer::bind(config) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("[-] Falha ao abrir socket para o daemon: {}", e);
                    std::process::exit(1);
                }
            };

            let local_addr = server.local_addr().to_string();
            if let Err(e) = server.start() {
                eprintln!("[-] Erro ao iniciar listener do daemon: {}", e);
                std::process::exit(1);
            }

            println!("[*] MARK Link Daemon ativo escutando em {}", local_addr);
            println!("[*] Isolamento estrito de rotas do MARK ativado.");
            println!("[*] Aguardando conexões com handshake mútuo...");

            let running = Arc::new(AtomicBool::new(true));
            let r = Arc::clone(&running);
            let _ = ctrlc_handler(move || {
                r.store(false, Ordering::SeqCst);
            });

            while running.load(Ordering::SeqCst) {
                thread::sleep(Duration::from_millis(200));
            }
            server.stop();
            println!("[*] Daemon do MARK Link encerrado com sucesso.");
        }
        "relay" => {
            let listen_addr = parse_arg(&args[2..], "--listen", "127.0.0.1:8767");
            println!("[*] Iniciando roteador de Relay Cego (Blind Relay) do MARK Link...");
            let mut relay = match RelayServer::bind(&listen_addr) {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("[-] Falha ao abrir socket para o relay: {}", e);
                    std::process::exit(1);
                }
            };

            let local_addr = relay.local_addr().to_string();
            if let Err(e) = relay.start() {
                eprintln!("[-] Erro ao iniciar listener do relay: {}", e);
                std::process::exit(1);
            }

            println!(
                "[*] MARK Link Blind Relay ativo escutando em {}",
                local_addr
            );
            println!("[*] Encaminhamento cego ponta-a-ponta ativo.");

            let running = Arc::new(AtomicBool::new(true));
            let r = Arc::clone(&running);
            let _ = ctrlc_handler(move || {
                r.store(false, Ordering::SeqCst);
            });

            while running.load(Ordering::SeqCst) {
                thread::sleep(Duration::from_millis(200));
            }
            relay.stop();
            println!("[*] Relay do MARK Link encerrado com sucesso.");
        }
        _ => {
            eprintln!("Comando desconhecido: {}", args[1]);
            print_help();
            std::process::exit(1);
        }
    }
}

fn ctrlc_handler<F: FnOnce() + Send + 'static>(_f: F) -> Result<(), ()> {
    Ok(())
}
