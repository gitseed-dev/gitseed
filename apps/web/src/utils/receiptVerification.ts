import type { GovernanceReceiptEnvelope, ReceiptPublicKey } from '../types'

export interface ReceiptVerificationResult {
  status: 'verified' | 'invalid' | 'unsupported'
  label: string
  detail: string
}

export function pemToDer(pem: string): ArrayBuffer {
  const clean = pem
    .replace(/-----BEGIN [A-Z0-9 ]+-----/g, '')
    .replace(/-----END [A-Z0-9 ]+-----/g, '')
    .replace(/\s+/g, '')
  if (!clean) {
    throw new Error('Empty PEM public key')
  }
  const binary = atob(clean)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

export function base64UrlToBuffer(b64url: string): ArrayBuffer {
  const trimmed = b64url.trim()
  if (!trimmed || /[^A-Za-z0-9\-_=+/]/.test(trimmed)) {
    throw new Error('Invalid Base64URL encoding')
  }
  const normalized = trimmed.replace(/-/g, '+').replace(/_/g, '/')
  const padLen = (4 - (normalized.length % 4)) % 4
  const padded = normalized + '='.repeat(padLen)
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

function deepJsonEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (typeof a !== typeof b || a === null || b === null) return false
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false
    for (let i = 0; i < a.length; i++) {
      if (!deepJsonEqual(a[i], b[i])) return false
    }
    return true
  }
  if (typeof a === 'object' && typeof b === 'object') {
    const recA = a as Record<string, unknown>
    const recB = b as Record<string, unknown>
    const keysA = Object.keys(recA)
    const keysB = Object.keys(recB)
    if (keysA.length !== keysB.length) return false
    for (const k of keysA) {
      if (!Object.prototype.hasOwnProperty.call(recB, k)) return false
      if (!deepJsonEqual(recA[k], recB[k])) return false
    }
    return true
  }
  return false
}

function isEd25519UnsupportedError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false
  const name = (err as { name?: string }).name || ''
  const message = (err as { message?: string }).message || ''
  return (
    name === 'NotSupportedError' ||
    /not supported|unrecognized algorithm|ed25519/i.test(message)
  )
}

/**
 * Verifies a signed GovernanceReceiptEnvelope in the browser using WebCrypto Ed25519
 * against the trusted public keys fetched from `/api/v1/receipts/public-key`.
 */
export async function verifyGovernanceReceiptClientSide(
  envelope: GovernanceReceiptEnvelope,
  publicKeyInfo: ReceiptPublicKey | null
): Promise<ReceiptVerificationResult> {
  if (envelope.algorithm !== 'Ed25519') {
    return {
      status: 'invalid',
      label: 'Signature Invalid / Tampered',
      detail: `Unsupported receipt signature algorithm: ${envelope.algorithm}`,
    }
  }

  if (typeof envelope.canonical_json !== 'string' || !envelope.canonical_json) {
    return {
      status: 'invalid',
      label: 'Signature Invalid / Tampered',
      detail: 'Missing canonical JSON payload on receipt envelope.',
    }
  }

  try {
    const parsedCanonical = JSON.parse(envelope.canonical_json) as unknown
    if (!deepJsonEqual(parsedCanonical, envelope.receipt)) {
      return {
        status: 'invalid',
        label: 'Signature Invalid / Tampered',
        detail: 'Receipt object does not match signed canonical_json payload.',
      }
    }
  } catch {
    return {
      status: 'invalid',
      label: 'Signature Invalid / Tampered',
      detail: 'Malformed canonical_json in receipt envelope.',
    }
  }

  if (!publicKeyInfo) {
    return {
      status: 'invalid',
      label: 'Signature Invalid / Tampered',
      detail: 'Unable to resolve control plane Ed25519 public key.',
    }
  }

  const allTrustedKeys: Array<{ key_id: string; public_key_pem: string }> = [
    {
      key_id: publicKeyInfo.key_id,
      public_key_pem: publicKeyInfo.public_key_pem,
    },
    ...(publicKeyInfo.previous_public_keys || []),
  ].filter((entry) => Boolean(entry.public_key_pem))

  const candidateKeys = envelope.key_id
    ? allTrustedKeys.filter((entry) => entry.key_id === envelope.key_id)
    : allTrustedKeys

  if (candidateKeys.length === 0) {
    return {
      status: 'invalid',
      label: 'Signature Invalid / Tampered',
      detail: `Signing key_id "${envelope.key_id}" is not trusted by /api/v1/receipts/public-key.`,
    }
  }

  let sigBuffer: ArrayBuffer
  try {
    sigBuffer = base64UrlToBuffer(envelope.signature)
    if (sigBuffer.byteLength !== 64) {
      return {
        status: 'invalid',
        label: 'Signature Invalid / Tampered',
        detail: `Invalid Ed25519 signature byte length (${sigBuffer.byteLength}).`,
      }
    }
  } catch {
    return {
      status: 'invalid',
      label: 'Signature Invalid / Tampered',
      detail: 'Malformed Base64URL signature.',
    }
  }

  const subtle = globalThis.crypto?.subtle
  if (!subtle) {
    return {
      status: 'unsupported',
      label: 'Server-Signed (Ed25519)',
      detail: 'WebCrypto API unavailable in this browser context.',
    }
  }

  const encodedBytes = new TextEncoder().encode(envelope.canonical_json)
  const payloadBuffer = new Uint8Array(encodedBytes).buffer

  for (const trusted of candidateKeys) {
    let derBuffer: ArrayBuffer
    try {
      derBuffer = pemToDer(trusted.public_key_pem)
    } catch {
      continue
    }

    let cryptoKey: CryptoKey
    try {
      cryptoKey = await subtle.importKey(
        'spki',
        derBuffer,
        { name: 'Ed25519' },
        false,
        ['verify']
      )
    } catch (err) {
      if (isEd25519UnsupportedError(err)) {
        return {
          status: 'unsupported',
          label: 'Server-Signed (Ed25519)',
          detail: 'Browser WebCrypto does not yet support Ed25519 verification.',
        }
      }
      return {
        status: 'invalid',
        label: 'Signature Invalid / Tampered',
        detail: 'Failed to import SPKI Ed25519 public key.',
      }
    }

    try {
      const valid = await subtle.verify(
        { name: 'Ed25519' },
        cryptoKey,
        sigBuffer,
        payloadBuffer
      )
      if (valid) {
        return {
          status: 'verified',
          label: 'Verified (Ed25519)',
          detail: `Client-verified with WebCrypto Ed25519 against key ${trusted.key_id}.`,
        }
      }
    } catch (err) {
      if (isEd25519UnsupportedError(err)) {
        return {
          status: 'unsupported',
          label: 'Server-Signed (Ed25519)',
          detail: 'Browser WebCrypto does not yet support Ed25519 verification.',
        }
      }
    }
  }

  return {
    status: 'invalid',
    label: 'Signature Invalid / Tampered',
    detail: 'Ed25519 signature verification failed against canonical receipt payload.',
  }
}
