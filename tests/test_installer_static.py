"""The installer, checked without a container: build it, read what it ships, check what it writes."""
import glob
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER = "__PAYLOAD_BELOW__"
HEAD = os.path.join(ROOT, "installer", "setup-head.sh")


def build(cwd=ROOT, env=None):
    return subprocess.run(["sh", "installer/build.sh"], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def built_path(repo=ROOT):
    return os.path.join(repo, "dist", "setup-sickbay.sh")


def payload_names(installer):
    """File names in the tarball that the installer unpacks, read the way the installer reads it."""
    script = f"sed -n '/^{MARKER}$/,$p' \"$1\" | tail -n +2 | base64 -d | tar -tzf -"
    out = subprocess.run(["sh", "-c", script, "sh", installer], check=True, capture_output=True, text=True).stdout
    return {line.rstrip("/") for line in out.splitlines()}


def mini_repo(tmp_path, static=False, deploy_env=None):
    """A copy of the build inputs with stand-in modules, so build.sh can be tried with and without static/."""
    for name in ("installer/build.sh", "installer/setup-head.sh", "ops/sickbay-backup"):
        os.makedirs(tmp_path / os.path.dirname(name), exist_ok=True)
        shutil.copy(os.path.join(ROOT, name), tmp_path / name)
    for name in ("app.py", "zzz_later_module.py"):
        (tmp_path / name).write_text("x = 1\n")
    if static:
        os.makedirs(tmp_path / "static" / "js")
        (tmp_path / "static" / "index.html").write_text("<p>hi</p>")
        (tmp_path / "static" / "js" / "a.js").write_text("export {};\n")
    if deploy_env is not None:
        os.makedirs(tmp_path / "deploy", exist_ok=True)
        (tmp_path / "deploy" / "deploy.env").write_text(deploy_env)
    return str(tmp_path)


def head_text():
    with open(HEAD) as f:
        return f.read()


def test_build_produces_valid_installer():
    build()
    subprocess.run(["sh", "-n", "dist/setup-sickbay.sh"], cwd=ROOT, check=True)


def test_installer_binds_loopback_never_sets_dev_email():
    head = head_text()
    assert "127.0.0.1:${SICKBAY_PORT}" in head and "0.0.0.0" not in head and "DEV_EMAIL" not in head
    build()
    with open(built_path()) as f:
        built = f.read()
    assert "0.0.0.0" not in built and "DEV_EMAIL" not in built


def test_every_setting_in_env_example_and_installer():
    with open(os.path.join(ROOT, "settings.py")) as f:
        src = f.read()
    names = set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', src)) | set(re.findall(r'_int\("([A-Z_]+)"', src))
    names -= {"DEV_EMAIL", "DATA_DIR"}
    assert {"SICKBAY_PORT", "ALLOWED_EMAILS", "USERS", "CF_TEAM_DOMAIN", "CF_AUD", "APP_NAME", "MAX_UPLOAD_MB"} <= names
    with open(os.path.join(ROOT, ".env.example")) as f:
        env_example = f.read()
    with open(os.path.join(ROOT, "deploy", "deploy.env.example")) as f:
        deploy_example = f.read()
    head = head_text()
    installer_settings = set(re.findall(r'^add_setting "\$CONF" ([A-Z_]+) ', head, re.M))
    exported = set(head.split("  export SICKBAY_PORT", 1)[1].split("\n", 1)[0].split()) | {"SICKBAY_PORT"}
    for name in names:
        assert re.search(rf"^{name}=", env_example, re.M), f"{name} missing from .env.example"
        assert name in installer_settings, f"{name} missing from the installer's settings list"
        assert name in exported, f"{name} is not exported to the service"
    assert installer_settings <= names, "installer writes a setting settings.py does not read"
    assert not re.search(r"^DEV_EMAIL=", env_example, re.M), "DEV_EMAIL is development only, commented out"
    assert "DEV_EMAIL" not in deploy_example


def test_payload_ships_every_top_level_py_and_static():
    build()
    shipped = payload_names(built_path())
    modules = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.py"))}
    assert modules, "no modules found"
    missing = {f"sickbay/{m}" for m in modules} - shipped
    assert not missing, f"not shipped: {sorted(missing)}"
    assert "sickbay/ops/sickbay-backup" in shipped
    if os.path.isdir(os.path.join(ROOT, "static")):
        assert any(n.startswith("sickbay/static/") for n in shipped)
    assert not any("__pycache__" in n or n.startswith("sickbay/tests") for n in shipped)


def test_build_ships_new_modules_by_glob_and_static_only_when_present(tmp_path):
    repo = mini_repo(tmp_path)
    build(repo)  # no static/: must not fail
    shipped = payload_names(built_path(repo))
    assert {"sickbay/app.py", "sickbay/zzz_later_module.py", "sickbay/ops/sickbay-backup"} <= shipped
    assert not any(n.startswith("sickbay/static") for n in shipped)

    os.makedirs(tmp_path / "with_static")
    repo = mini_repo(tmp_path / "with_static", static=True)
    build(repo)
    shipped = payload_names(built_path(repo))
    assert {"sickbay/static/index.html", "sickbay/static/js/a.js", "sickbay/zzz_later_module.py"} <= shipped


def test_default_port_is_8774():
    head = head_text()
    assert "DEFAULT_PORT=8774" in head and ": ${SICKBAY_PORT:=8774}" in head
    with open(os.path.join(ROOT, "settings.py")) as f:
        assert '_int("SICKBAY_PORT", 8774, 1)' in f.read()
    with open(os.path.join(ROOT, ".env.example")) as f:
        assert re.search(r"^SICKBAY_PORT=8774$", f.read(), re.M)


def test_names_paths_and_packages():
    head = head_text()
    for line in ("NAME=sickbay", "APP_DIR=/opt/$NAME", "DATA_DIR=/var/lib/$NAME", "BACKUP_DIR=/var/backups/$NAME",
                 "CONF=/etc/conf.d/$NAME", "BACKUP_JOB=/etc/periodic/daily/$NAME-backup"):
        assert line in head, line
    assert "apk add --no-cache python3 py3-flask py3-gunicorn py3-jwt py3-cryptography sqlite tar ca-certificates" in head
    assert 'chmod 600 "$CONF"; chown root:root "$CONF"' in head and 'chmod 750 "$DATA_DIR"' in head
    assert 'chmod 700 "$BACKUP_DIR"' in head and 'mkdir -p "$DATA_DIR/files"' in head
    assert 'install -m 755 "$APP_DIR/ops/sickbay-backup" "$BACKUP_JOB"' in head


def test_uninstall_keeps_data_config_and_backups():
    uninstall = head_text().split('if [ -n "$UNINSTALL" ]; then', 1)[1].split("exit 0", 1)[0]
    assert 'rm -f "$INITD" "$BACKUP_JOB"' in uninstall and 'rm -rf "$APP_DIR"' in uninstall
    kept = uninstall.split("Kept", 1)[1]
    assert "$DATA_DIR" in kept and "$CONF" in kept and "$BACKUP_DIR" in kept
    assert not re.search(r"rm .*\$(DATA_DIR|CONF|BACKUP_DIR)", uninstall)


def test_deploy_env_defaults_are_baked_in_and_the_environment_wins(tmp_path):
    repo = mini_repo(tmp_path, deploy_env='ALLOWED_EMAILS=alex@example.com,sam@example.com\nAPP_NAME="Logbook"\nCF_AUD=\n'
                                          "SICKBAY_PORT=9999\nDEV_EMAIL=alex@example.com\n")
    build(repo)
    with open(built_path(repo)) as f:
        out = f.read()
    assert ': "${ALLOWED_EMAILS:=alex@example.com,sam@example.com}"' in out
    assert ': "${APP_NAME:=Logbook}"' in out
    assert "CF_AUD:=" not in out  # empty values are not baked in
    assert "DEV_EMAIL" not in out and "9999" not in out  # only the household keys are read
    subprocess.run(["sh", "-n", built_path(repo)], check=True)


@pytest.mark.parametrize("bad", ['a"b', "a$b", "a`b", "a\\b"])
def test_deploy_env_rejects_characters_that_would_break_the_installer(tmp_path, bad):
    repo = mini_repo(tmp_path, deploy_env=f"USERS={bad}\n")
    r = subprocess.run(["sh", "installer/build.sh"], cwd=repo, capture_output=True, text=True)
    assert r.returncode != 0 and "USERS" in r.stdout


def test_a_mistyped_installer_argument_is_refused():
    """Run the installer's argument check alone: it must not fall through to an install."""
    head = head_text()
    block = head.split("UNINSTALL=\n", 1)[1].split("# Household values", 1)[0]
    for args, status in (([], 0), (["--uninstall"], 0), (["--uninstal"], 1), (["--instance", "x"], 1), (["a", "b"], 1)):
        r = subprocess.run(["sh", "-c", "set -eu\nUNINSTALL=\n" + block + "echo ok", "sh", *args],
                           capture_output=True, text=True)
        assert r.returncode == status, (args, r.stdout, r.stderr)
