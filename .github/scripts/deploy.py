import ftplib
import hashlib
import os
from pathlib import Path, PurePosixPath
import ssl
import subprocess


BASELINE = "0e6b3e42bf4cb5aa8cdf5688dfa093e9613f6662"
VERIFY_FILES = ("images/pic18.jpg", "image/UV LED/1934.jpg")
PUBLIC_EXTENSIONS = {
    ".css", ".gif", ".htm", ".html", ".ico", ".jpeg", ".jpg", ".js",
    ".png", ".svg", ".webp", ".woff", ".woff2",
}


def command(*args):
    return subprocess.check_output(args)


def remote_digest(ftp, path):
    digest = hashlib.sha256()
    ftp.retrbinary(f"RETR {path}", digest.update)
    return digest.digest()


def verify(ftp, path):
    return remote_digest(ftp, path) == hashlib.sha256(Path(path).read_bytes()).digest()


def deployment_base(head):
    repo = os.environ["GITHUB_REPOSITORY"]
    route = f"repos/{repo}/actions/workflows/deploy.yml/runs?branch=main&status=success"
    revisions = command("gh", "api", route, "--jq", ".workflow_runs[].head_sha")
    for revision in revisions.decode().splitlines() + [BASELINE]:
        if subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, head], check=False
        ).returncode == 0:
            return revision
    raise RuntimeError("No verified ancestor found. Check main branch history.")


def changed_paths(base, head):
    output = command(
        "git", "diff", "--name-only", "--no-renames", "--diff-filter=AM",
        "-z", base, head,
    )
    for name in output.decode().split("\0"):
        path = PurePosixPath(name)
        if any(part.startswith(".") or part == "_vti_cnf" for part in path.parts):
            continue
        if path.suffix.lower() in PUBLIC_EXTENSIONS or name in {"robots.txt", "sitemap.xml"}:
            source = Path(name)
            if source.is_symlink() or not source.is_file():
                raise RuntimeError(f"Expected a regular file: {name!r}")
            if "\r" in name or "\n" in name:
                raise RuntimeError("A filename contains a line break.")
            yield name


def main():
    password = os.environ.get("FTPS_PASSWORD")
    if not password:
        raise RuntimeError("The deployment password is missing.")
    head = os.environ["GITHUB_SHA"]
    repo = os.environ["GITHUB_REPOSITORY"]
    latest = command("gh", "api", f"repos/{repo}/git/ref/heads/main", "--jq", ".object.sha")
    if latest.decode().strip() != head:
        raise RuntimeError("A newer main revision is available. Deploy that revision.")
    base = deployment_base(head)
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    context.load_verify_locations(Path(__file__).with_name("certum-ov-tls-g2-r39.pem"))
    with ftplib.FTP_TLS(context=context, timeout=30) as ftp:
        ftp.connect("211.72.204.227", 21)
        # The provider's FTPS certificate identifies url.com.tw.
        ftp.host = "url.com.tw"
        ftp.login("ledcase.com", password)
        ftp.prot_p()
        ftp.cwd("/")
        if os.environ.get("DEPLOY_ENABLED") != "true":
            for path in VERIFY_FILES:
                if not verify(ftp, path):
                    raise RuntimeError(f"Baseline content differs: {path}")
            print("Verified both live image files over FTPS. No files written.")
            return
        for path in changed_paths(base, head):
            with Path(path).open("rb") as source:
                ftp.storbinary(f"STOR {path}", source)
            if not verify(ftp, path):
                raise RuntimeError(f"Uploaded content differs: {path}")
            print(f"Deployed and verified {path}")


if __name__ == "__main__":
    main()
