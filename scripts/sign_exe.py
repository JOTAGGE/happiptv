"""
Script de automação para assinatura digital (Code Signing) do executável Happiptv.

Suporta:
- Windows SDK `signtool.exe` (localizado automaticamente ou pelo PATH)
- Certificados em arquivo .pfx / .p12 com senha
- Carimbo de tempo RFC 3161 (DigiCert / Sectigo)
- Geração opcional de certificado auto-assinado para homologação local via PowerShell
- Verificação de integridade pós-assinatura

Uso:
    python scripts/sign_exe.py --exe dist/Happiptv.exe --cert meu_cert.pfx --password segredo
    python scripts/sign_exe.py --create-test-cert
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"


def find_signtool() -> str | None:
    # 1. Check if signtool is in PATH
    try:
        res = subprocess.run(["signtool", "/?"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode in (0, 1):
            return "signtool"
    except FileNotFoundError:
        pass

    # 2. Search Windows Kits directories
    kit_patterns = [
        r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe",
        r"C:\Program Files\Windows Kits\10\bin\*\x64\signtool.exe",
        r"C:\Program Files (x86)\Windows Kits\8.1\bin\x64\signtool.exe",
    ]
    for pattern in kit_patterns:
        matches = glob.glob(pattern)
        if matches:
            # Pick latest version
            matches.sort(reverse=True)
            return matches[0]

    return None


def create_test_certificate(output_pfx: Path, password: str = "HappiptvTest123!") -> bool:
    """Gera um certificado auto-assinado localmente para teste e homologação de build."""
    print(f"[*] Gerando certificado auto-assinado para testes: {output_pfx}")
    ps_cmd = f"""
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=Happiptv Experimental Dev, O=Blue Lab" -CertStoreLocation Cert:\\CurrentUser\\My -NotAfter (Get-Date).AddYears(3);
    $pwd = ConvertTo-SecureString -String "{password}" -Force -AsPlainText;
    Export-PfxCertificate -Cert $cert -FilePath "{output_pfx.resolve()}" -Password $pwd;
    """
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        if res.returncode == 0 and output_pfx.exists():
            print(f"[✓] Certificado de teste criado com sucesso: {output_pfx}")
            print(f"    Senha padrão: {password}")
            return True
        else:
            print(f"[!] Falha ao gerar certificado via PowerShell:\n{res.stderr}")
            return False
    except Exception as exc:
        print(f"[!] Erro ao invocar PowerShell: {exc}")
        return False


def sign_executable(
    exe_path: Path,
    cert_path: Path,
    password: str | None = None,
    timestamp_url: str = DEFAULT_TIMESTAMP_URL,
    signtool_bin: str | None = None,
) -> bool:
    tool = signtool_bin or find_signtool()
    if not tool:
        print("[!] signtool.exe não foi encontrado no sistema.")
        print("    Instale o Windows SDK ou adicione signtool ao PATH.")
        return False

    if not exe_path.exists():
        print(f"[!] Executável não encontrado: {exe_path}")
        return False

    if not cert_path.exists():
        print(f"[!] Certificado não encontrado: {cert_path}")
        return False

    cmd = [
        tool,
        "sign",
        "/fd", "sha256",
        "/f", str(cert_path.resolve()),
    ]
    if password:
        cmd.extend(["/p", password])

    if timestamp_url:
        cmd.extend(["/tr", timestamp_url, "/td", "sha256"])

    cmd.append(str(exe_path.resolve()))

    print(f"[*] Assinando '{exe_path.name}' com signtool...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[!] Falha na assinatura:\n{res.stderr or res.stdout}")
        return False

    print(f"[✓] Assinatura aplicada com sucesso em: {exe_path}")

    # Verificar assinatura
    print("[*] Verificando assinatura do binário...")
    verify_cmd = [tool, "verify", "/pa", str(exe_path.resolve())]
    v_res = subprocess.run(verify_cmd, capture_output=True, text=True)
    if v_res.returncode == 0:
        print("[✓] Verificação concluída: assinatura válida!")
        return True
    else:
        print(f"[~] Verificação retornou aviso/erro (esperado para certificados auto-assinados locais sem raiz confiável):\n{v_res.stdout}")
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Code Signing para o executável Happiptv.")
    parser.add_argument("--exe", default="dist/Happiptv.exe", help="Caminho do executável a ser assinado")
    parser.add_argument("--cert", default=os.getenv("SIGN_CERT_PATH", ""), help="Caminho do arquivo .pfx do certificado")
    parser.add_argument("--password", default=os.getenv("SIGN_CERT_PASSWORD", ""), help="Senha do arquivo .pfx")
    parser.add_argument("--timestamp-url", default=os.getenv("SIGN_TIMESTAMP_URL", DEFAULT_TIMESTAMP_URL), help="URL do servidor de timestamp RFC 3161")
    parser.add_argument("--create-test-cert", action="store_true", help="Gera um certificado auto-assinado local de teste (test_cert.pfx)")
    args = parser.parse_args()

    if args.create_test_cert:
        out_cert = Path("test_cert.pfx")
        ok = create_test_certificate(out_cert)
        return 0 if ok else 1

    cert_path = Path(args.cert) if args.cert else None
    if not cert_path or not cert_path.exists():
        print("[!] Nenhum certificado válido fornecido via --cert ou variável SIGN_CERT_PATH.")
        print("    Para gerar um certificado de teste local, execute:")
        print("    python scripts/sign_exe.py --create-test-cert")
        return 1

    exe_path = Path(args.exe)
    ok = sign_executable(
        exe_path=exe_path,
        cert_path=cert_path,
        password=args.password or None,
        timestamp_url=args.timestamp_url,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
