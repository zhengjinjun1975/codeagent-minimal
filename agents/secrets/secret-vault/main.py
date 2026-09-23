#!/usr/bin/env python3
"""secret-vault 原子壳（open_source:true）——密钥加密安全存储（P0 安全边界）。

借鉴 Codex `keyring-store/`（系统 keyring 安全存储）思路，用纯 stdlib 做**静态加密**：
主口令经 PBKDF2-HMAC-SHA256 派生密钥（随机盐 + 迭代），per-secret 随机 IV 生成
HMAC 密钥流做 XOR 流式加密，密文落盘（可设 0600 权限），内存按需解密。

诚实边界（如实标注）：纯 stdlib 无第三方密码学库，此加密是**静态混淆**——
防明文直接落盘被读，非经过审计的强加密，不可替代 HSM/系统 keyring/商用加密。

能力（纯 stdlib，数据不出厂）：
  secrets.encrypt — 加密明文 → {salt, iv, ciphertext}(b64)
  secrets.decrypt — 解密回明文（需主口令）
  secrets.store   — 按名加密落盘（返回不含明文）
  secrets.load    — 按名解密读取（按需解密）
  secrets.mask    — 掩码（仅露前缀/后缀）
  secrets.vault   — 列出已存密钥名（不含值）
"""
import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets_mod
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

_KDF_ITERS = 120_000
_BLOCK = 32  # HMAC-SHA256 输出字节


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or not passphrase:
        raise ValueError("主口令不能为空")
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"),
                               salt, _KDF_ITERS, dklen=32)


def _stream(key: bytes, iv: bytes, length: int):
    """HMAC(key, iv||counter) 密钥流生成器。"""
    counter = 0
    while length > 0:
        block = hmac.new(key, iv + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        yield block[:length]
        length -= len(block[:length])
        counter += 1


def encrypt(plaintext: str, passphrase: str) -> dict:
    if not isinstance(plaintext, str):
        raise ValueError("明文必须是字符串")
    salt = _secrets_mod.token_bytes(16)
    iv = _secrets_mod.token_bytes(16)
    key = _derive_key(passphrase, salt)
    data = plaintext.encode("utf-8")
    stream = b"".join(_stream(key, iv, len(data)))
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(key, salt + iv + cipher, hashlib.sha256).digest()
    return {"salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(),
            "ciphertext": base64.b64encode(cipher).decode(),
            "tag": base64.b64encode(tag).decode(),
            "kdf": "pbkdf2-hmac-sha256", "iterations": _KDF_ITERS,
            "algo": "hmac-stream-xor(obfuscation-at-rest)"}


def decrypt(bundle: dict, passphrase: str) -> str:
    salt = base64.b64decode(bundle["salt"])
    iv = base64.b64decode(bundle["iv"])
    cipher = base64.b64decode(bundle["ciphertext"])
    tag = base64.b64decode(bundle["tag"])
    key = _derive_key(passphrase, salt)
    expect = hmac.new(key, salt + iv + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(expect, tag):
        raise ValueError("解密失败：口令错误或密文被篡改")
    stream = b"".join(_stream(key, iv, len(cipher)))
    return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")


def mask(value: str, keep: int = 4) -> str:
    if not isinstance(value, str) or not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep * 2) + value[-keep:]


class SecretVaultAgent(AtomicAgent):
    name = "secret-vault"
    version = "0.1.0"
    domain = "secrets"
    description = ("密钥加密存储原子（P0，借鉴Codex keyring-store）: api_key等敏感值PBKDF2派生密钥+"
                   "HMAC密钥流XOR静态加密落盘, 按需解密, 掩码展示。纯stdlib数据不出厂(静态混淆, 非强加密如实标注)。")
    provides = ["secrets.encrypt", "secrets.decrypt", "secrets.store",
                "secrets.load", "secrets.mask", "secrets.vault"]
    depends_on = []
    inputs = ["plaintext", "value", "passphrase", "name", "vault_dir", "bundle", "keep"]
    outputs = ["salt", "iv", "ciphertext", "tag", "decrypted", "stored", "name", "masked", "names"]

    def _register_defaults(self):
        self.register("secrets.encrypt", self._encrypt)
        self.register("secrets.decrypt", self._decrypt)
        self.register("secrets.store", self._store)
        self.register("secrets.load", self._load)
        self.register("secrets.mask", self._mask)
        self.register("secrets.vault", self._vault)

    def _passphrase(self, passphrase=None):
        return passphrase or os.environ.get("CODEAGENT_MASTER_KEY") or ""

    def _vault_dir(self, vault_dir=None):
        return vault_dir or os.path.join(REPO_ROOT, ".codeagent", "secrets")

    def _encrypt(self, plaintext=None, passphrase=None):
        pw = self._passphrase(passphrase)
        if not pw:
            return self._envelope(False, degraded=True, error="缺主口令(passphrase 或 env CODEAGENT_MASTER_KEY)")
        try:
            return encrypt(plaintext, pw)
        except Exception as e:
            return self._envelope(False, degraded=True, error=f"{type(e).__name__}: {e}")

    def _decrypt(self, bundle=None, passphrase=None):
        pw = self._passphrase(passphrase)
        if not bundle or not pw:
            return self._envelope(False, degraded=True, error="缺 bundle 或主口令")
        try:
            return {"decrypted": decrypt(bundle, pw)}
        except Exception as e:
            return self._envelope(False, degraded=True, error=f"{type(e).__name__}: {e}")

    def _store(self, name=None, value=None, passphrase=None, vault_dir=None):
        pw = self._passphrase(passphrase)
        if not name or value is None or not pw:
            return self._envelope(False, degraded=True, error="缺 name/value/主口令")
        try:
            bundle = encrypt(value, pw)
            vd = self._vault_dir(vault_dir)
            os.makedirs(vd, exist_ok=True)
            path = os.path.join(vd, name.replace(os.sep, "_") + ".json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(bundle, f)
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            except Exception:
                pass
            return {"stored": True, "name": name, "path": path,
                    "masked": mask(value), "note": "密文已落盘, 返回不含明文"}
        except Exception as e:
            return self._envelope(False, degraded=True, error=f"{type(e).__name__}: {e}")

    def _load(self, name=None, passphrase=None, vault_dir=None):
        pw = self._passphrase(passphrase)
        if not name or not pw:
            return self._envelope(False, degraded=True, error="缺 name 或主口令")
        try:
            vd = self._vault_dir(vault_dir)
            path = os.path.join(vd, name.replace(os.sep, "_") + ".json")
            if not os.path.isfile(path):
                return self._envelope(False, degraded=True, error=f"密钥不存在: {name}")
            bundle = json.load(open(path, encoding="utf-8"))
            plain = decrypt(bundle, pw)
            return {"name": name, "value": plain, "decrypted": plain, "masked": mask(plain)}
        except Exception as e:
            return self._envelope(False, degraded=True, error=f"{type(e).__name__}: {e}")

    def _mask(self, value=None, keep=4):
        if value is None:
            return self._envelope(False, degraded=True, error="缺 value 入参")
        return {"masked": mask(str(value), keep=keep), "length": len(str(value))}

    def _vault(self, vault_dir=None):
        vd = self._vault_dir(vault_dir)
        if not os.path.isdir(vd):
            return {"names": [], "count": 0}
        names = sorted(f[:-5] for f in os.listdir(vd) if f.endswith(".json"))
        return {"names": names, "count": len(names)}


agent = SecretVaultAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(SecretVaultAgent(), run_args={
        "capability": {"default": "secrets.store", "choices": list(SecretVaultAgent.provides)},
        "plaintext": {}, "value": {}, "passphrase": {}, "name": {},
    }))
