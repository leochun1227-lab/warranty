"""Automatic private configuration for the existing daily task.

The portable package holds only a hybrid RSA-OAEP/AES-GCM envelope. The existing
Firebase service-account private key unlocks it on the planning PC. No network,
installation, C4C mutation, or Firebase mutation occurs during bootstrap.
"""
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path

from issue_ai import ROOT, IssueAIError, config_path, read_env, validate_config

AAD = b'WarrantyIssueAI bootstrap v1'
ALGORITHM = 'RSA-OAEP-SHA256+A256GCM'
PACKAGE_KEYS = {'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_RESPONSES_URL',
                'ISSUE_AI_MODEL', 'C4C_ISSUE_USERNAME', 'C4C_ISSUE_PASSWORD',
                'C4C_ISSUE_AI_ENABLED', 'C4C_ISSUE_POSITION_ENABLED', 'ISSUE_AI_MAX_PER_RUN', 'ISSUE_AI_BATCH_SIZE'}


def private_key(service_account):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    raw=json.loads(Path(service_account).read_text(encoding='utf-8-sig'))
    key=serialization.load_pem_private_key(raw['private_key'].encode(), password=None)
    if not isinstance(key, RSAPrivateKey):
        raise IssueAIError('Issue AI bootstrap requires the existing RSA Firebase credential')
    return key


def fingerprint(key):
    from cryptography.hazmat.primitives import serialization
    public=key.public_key() if hasattr(key,'private_numbers') else key
    encoded=public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(encoded).hexdigest()


def oaep():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    return padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=AAD)


def check_values(config):
    if not isinstance(config,dict) or set(config)-PACKAGE_KEYS:
        raise IssueAIError('Unexpected Issue AI bootstrap settings')
    if any(not isinstance(v,str) or any(c in v for c in ('\n','\r','\x00')) for v in config.values()):
        raise IssueAIError('Invalid Issue AI bootstrap setting value')
    if config.get('C4C_ISSUE_POSITION_ENABLED')=='1':
        from issue_positions import validate_position_config
        validate_position_config(config)
    else:
        validate_config(config)


def seal(config, service_account):
    """Build-time helper. Return encrypted bytes only; never include the unlock key."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    check_values(config)
    key=private_key(service_account)
    session_key=AESGCM.generate_key(bit_length=256)
    nonce=os.urandom(12)
    encode=lambda b:base64.b64encode(b).decode('ascii')
    return {'version':1,'algorithm':ALGORITHM,'keyFingerprint':fingerprint(key),
        'wrappedKey':encode(key.public_key().encrypt(session_key,oaep())),
        'nonce':encode(nonce),
        'ciphertext':encode(AESGCM(session_key).encrypt(nonce,json.dumps(config).encode(),AAD))}


def unseal(envelope, service_account):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        if envelope.get('version')!=1 or envelope.get('algorithm')!=ALGORITHM:
            raise IssueAIError('Unsupported Issue AI bootstrap format')
        key=private_key(service_account)
        if fingerprint(key)!=envelope.get('keyFingerprint'):
            raise IssueAIError('Issue AI configuration is encrypted for a different Firebase key; existing ticket refresh is unaffected')
        decode=lambda s:base64.b64decode(s,validate=True)
        session_key=key.decrypt(decode(envelope['wrappedKey']),oaep())
        payload=AESGCM(session_key).decrypt(decode(envelope['nonce']),decode(envelope['ciphertext']),AAD)
        config=json.loads(payload)
        check_values(config)
        return config
    except IssueAIError:
        raise
    except Exception:
        # Crypto/JSON exceptions must not print tokens, private keys or plaintext.
        raise IssueAIError('Issue AI encrypted configuration could not be verified') from None


def ensure_private_configuration(service_account, *, root=None, target=None):
    """Idempotent per scheduled-task account; preserve existing settings/opt-outs."""
    root=Path(root or ROOT).resolve()
    target=Path(target or config_path()).resolve()
    if target.is_file():
        return 'existing'
    bundle=root/'.issue-ai'/'bootstrap.json'
    if not bundle.is_file():
        return 'absent'
    if target.is_relative_to(root):
        raise IssueAIError('Automatic Issue AI settings must be outside the project/web directory')
    config=unseal(json.loads(bundle.read_text(encoding='utf-8')),service_account)
    text='# Private daily-task settings. Never publish or commit.\n'
    text+='\n'.join(k+'="'+v+'"' for k,v in config.items())+'\n'
    target.parent.mkdir(parents=True,exist_ok=True)
    # Publish only a fully written/verified private file. An atomic hard-link create
    # cannot replace another process's file; LOCALAPPDATA uses the account's ACL.
    descriptor,temp_name=tempfile.mkstemp(prefix='issue-ai-',suffix='.tmp',dir=target.parent)
    temporary=Path(temp_name)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if read_env(temporary)!=config:
            raise IssueAIError('Issue AI private configuration verification failed')
        try:
            os.link(temporary,target)
        except FileExistsError:
            return 'existing'
    except Exception:
        raise IssueAIError('Issue AI private configuration could not be saved') from None
    finally:
        temporary.unlink(missing_ok=True)
    return 'created'
