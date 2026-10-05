"""Google Drive wrapper for DasTwitchy via hatch_gws_cli."""
import json
import shutil
import subprocess

_GWS = shutil.which("hatch_gws_cli") or "/opt/hatch/bin/hatch_gws_cli"


def _run(cmd, timeout=120):
    try:
        r = subprocess.run(
            [_GWS, "drive"] + cmd,
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        return 1, "", str(e)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def connected(timeout=30):
    rc, out, _ = _run(["status"], timeout=timeout)
    if rc != 0:
        return False
    try:
        return json.loads(out).get("status") == "connected"
    except Exception:
        return False


def _find_folder(name, parent_id):
    q = (
        f"name = '{name}' and '{parent_id}' in parents "
        "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    rc, out, _ = _run(
        ["files", "list", "--params",
         json.dumps({"q": q, "fields": "files(id,name)", "pageSize": 10})]
    )
    if rc != 0:
        return None
    try:
        files = json.loads(out).get("files", [])
    except Exception:
        return None
    return files[0]["id"] if files else None


def _create_folder(name, parent_id):
    rc, out, _ = _run(
        ["files", "create",
         "--params", json.dumps({"ignoreDefaultVisibility": True}),
         "--json", json.dumps({
             "name": name,
             "mimeType": "application/vnd.google-apps.folder",
             "parents": [parent_id]})]
    )
    if rc != 0:
        return None
    try:
        return json.loads(out).get("id")
    except Exception:
        return None


def ensure_tree(root_name, originals_name, parts_name):
    """Return (originals_id, parts_id); creates folders as needed."""
    root = _find_folder(root_name, "root") or _create_folder(root_name, "root")
    if not root:
        raise RuntimeError("could not find/create Drive root folder")
    orig = _find_folder(originals_name, root) or _create_folder(originals_name, root)
    parts = _find_folder(parts_name, root) or _create_folder(parts_name, root)
    if not orig or not parts:
        raise RuntimeError("could not find/create Drive subfolders")
    return orig, parts


def upload(local_path, parent_id, name=None, timeout=900):
    """Upload a file; returns (file_id, error)."""
    cmd = ["+upload", str(local_path), "--parent", parent_id, "--format", "json"]
    if name:
        cmd += ["--name", name]
    rc, out, err = _run(cmd, timeout=timeout)
    if rc != 0:
        return None, (err or out or "upload failed")[-300:]
    try:
        data = json.loads(out)
        fid = data.get("id") or data.get("webViewLink")
    except Exception:
        fid = None
    if not fid:
        fid = out.strip().split()[-1] if out.strip() else None
    return fid, None


def web_link(file_id):
    rc, out, _ = _run(
        ["files", "get", "--params",
         json.dumps({"fileId": file_id, "fields": "webViewLink"})]
    )
    if rc != 0:
        return None
    try:
        return json.loads(out).get("webViewLink")
    except Exception:
        return None
