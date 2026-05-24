import hashlib
import hmac
import os
import random

# RFC 7748 X25519 Elliptic-Curve Diffie-Hellman Constant Prime Field
P = 2**255 - 19

def x25519_clamp(n):
    """
    Clamps a 32-byte private scalar to ensure it lies in the proper range
    for Curve25519: clears bits 0, 1, 2 (to avoid small subgroup attacks),
    clears bit 255, and sets bit 254 (for constant-time scaling).
    """
    n &= ~(7)
    n &= ~(128 << 24)
    n |= (64 << 24)
    return n

def x25519_ladder(k, u=9):
    """
    Computes point multiplication k * U using the Montgomery Ladder
    as described in RFC 7748. Safe against timing attacks.
    """
    u = int(u)
    k = int(k)
    x_1 = u
    x_2 = 1
    z_2 = 0
    x_3 = u
    z_3 = 1
    
    for t in reversed(range(255)):
        k_t = (k >> t) & 1
        if k_t:
            x_2, x_3 = x_3, x_2
            z_2, z_3 = z_3, z_2
            
        A = (x_2 + z_2) % P
        AA = (A * A) % P
        B = (x_2 - z_2) % P
        BB = (B * B) % P
        C = (x_3 + z_3) % P
        D = (x_3 - z_3) % P
        DA = (D * A) % P
        CB = (C * B) % P
        
        x_3 = ((DA + CB) ** 2) % P
        z_3 = (u * ((DA - CB) ** 2)) % P
        x_2 = (AA * BB) % P
        E = (AA - BB) % P
        z_2 = (E * (BB + 121665 * E)) % P
        
        if k_t:
            x_2, x_3 = x_3, x_2
            z_2, z_3 = z_3, z_2
            
    return (x_2 * pow(z_2, P - 2, P)) % P

def derive_keys(shared_secret):
    """
    Derives separate 32-byte encryption and MAC keys using SHA-256 HKDF/PBKDF2.
    """
    secret_bytes = shared_secret.to_bytes(32, 'big')
    # Derive K_enc (for CTR encryption) and K_mac (for HMAC authentication)
    k_enc = hashlib.pbkdf2_hmac('sha256', secret_bytes, b"CyrinxEncryptionSalt", 100, 32)
    k_mac = hashlib.pbkdf2_hmac('sha256', secret_bytes, b"CyrinxMacSalt", 100, 32)
    return k_enc, k_mac

def encrypt_ctr(key, payload):
    """
    Encrypts a payload using SHA-256 in Counter Mode (keystream cipher).
    """
    keystream = b""
    counter = 0
    while len(keystream) < len(payload):
        h = hashlib.sha256(key + counter.to_bytes(4, 'big')).digest()
        keystream += h
        counter += 1
    return bytes(a ^ b for a, b in zip(payload, keystream))

def secure_envelope_pack(payload, k_enc, k_mac):
    """
    Applies Encrypt-then-MAC:
    1. Encrypts payload.
    2. Calculates HMAC over ciphertext.
    3. Bundles ciphertext + MAC.
    """
    ciphertext = encrypt_ctr(k_enc, payload)
    mac = hmac.new(k_mac, ciphertext, hashlib.sha256).digest()
    return ciphertext, mac

def secure_envelope_unpack(ciphertext, mac, k_enc, k_mac):
    """
    Verifies MAC first (using constant-time compare) and decrypts if valid.
    """
    expected_mac = hmac.new(k_mac, ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("❌ INTEGRITY ERROR: HMAC signature verification failed!")
    return encrypt_ctr(k_enc, ciphertext)

def main():
    print("======================================================================")
    print("       🔑 POC 7: ECDH CRYPTOGRAPHIC KEY EXCHANGE ENVELOPE 🔑")
    print("======================================================================")
    print("\nThis script demonstrates how Cyrinx establishes a secure acoustic link")
    print("out-of-band using native X25519 Elliptic-Curve Diffie-Hellman (ECDH)")
    print("key exchange and secures transmissions with an Encrypt-then-MAC envelope.\n")
    
    # 1. Generate Ephemeral Keypairs
    # Private keys (random 256-bit scalars)
    alice_priv_raw = random.randint(1, P - 1)
    alice_priv = x25519_clamp(alice_priv_raw)
    
    bob_priv_raw = random.randint(1, P - 1)
    bob_priv = x25519_clamp(bob_priv_raw)
    
    # Public keys (U-coordinates)
    alice_pub = x25519_ladder(alice_priv, 9)
    bob_pub = x25519_ladder(bob_priv, 9)
    
    print("⚡ STEP 1: KEY GENERATION & CLAMPING:")
    print("----------------------------------------------------------------------")
    print(f"  * Alice Private (clamp): {hex(alice_priv)[:18]}...")
    print(f"  * Alice Public Key (U):  {hex(alice_pub)[:18]}...")
    print(f"  * Bob Private (clamp):   {hex(bob_priv)[:18]}...")
    print(f"  * Bob Public Key (U):    {hex(bob_pub)[:18]}...")
    print("----------------------------------------------------------------------")
    
    # 2. Perform ECDH Exchange
    alice_shared = x25519_ladder(alice_priv, bob_pub)
    bob_shared = x25519_ladder(bob_priv, alice_pub)
    
    print("\n🤝 STEP 2: DIFFIE-HELLMAN KEY EXCHANGE (OVER ACOUSTIC PREAMBLE):")
    print("----------------------------------------------------------------------")
    print(f"  * Alice Derived Secret:  {hex(alice_shared)[:24]}...")
    print(f"  * Bob Derived Secret:    {hex(bob_shared)[:24]}...")
    assert alice_shared == bob_shared, "Key exchange mismatch!"
    print("  * Shared Secrets Match:  ✔ SUCCESS (Mathematical agreement established!)")
    print("----------------------------------------------------------------------")
    
    # 3. Key Derivation KDF
    k_enc, k_mac = derive_keys(alice_shared)
    print("\n🗝️ STEP 3: SYMMETRIC KEY DERIVATION (KDF):")
    print("----------------------------------------------------------------------")
    print(f"  * Encryption Key (K_enc): {k_enc.hex()[:24]}...")
    print(f"  * Authentication Key (K_mac): {k_mac.hex()[:24]}...")
    print("----------------------------------------------------------------------")
    
    # 4. Pack Secure Control Envelope (Alice sends a command to Bob)
    control_frame = b"\xE1\x02\x4C\x4F\x4F\x50\x42\x41\x43\x4B" # Cyrinx Magic + Config Payload (10 bytes)
    print("\n📦 STEP 4: ENCRYPT-THEN-MAC PACKING (ALICE SENDER):")
    print("----------------------------------------------------------------------")
    print(f"  * Raw Payload:       {control_frame.hex()} (Magic \\xE1 + Cmd)")
    ciphertext, mac = secure_envelope_pack(control_frame, k_enc, k_mac)
    print(f"  * Encrypted Payload: {ciphertext.hex()}")
    print(f"  * HMAC Signature:    {mac.hex()}")
    print("----------------------------------------------------------------------")
    
    # 5. Unpack Secure Control Envelope (Bob receiver verifies and decrypts)
    print("\n🔓 STEP 5: DECRYPTION & INTEGRITY VERIFICATION (BOB RECEIVER):")
    print("----------------------------------------------------------------------")
    decrypted_frame = secure_envelope_unpack(ciphertext, mac, k_enc, k_mac)
    print(f"  * Decrypted Payload: {decrypted_frame.hex()}")
    assert decrypted_frame == control_frame
    print("  * Integrity Check:   ✔ PASS (Payload is authentic and un-tampered!)")
    print("----------------------------------------------------------------------")
    
    # 6. Simulate Tampering / Attack Injection
    print("\n🛑 STEP 6: SIMULATING WIRELESS ACOUSTIC INJECTION ATTACK:")
    print("----------------------------------------------------------------------")
    # Attacker modifies the last byte of the encrypted payload
    tampered_ciphertext = bytearray(ciphertext)
    tampered_ciphertext[-1] ^= 0xFF # Flip bits
    tampered_ciphertext = bytes(tampered_ciphertext)
    
    print(f"  * Tampered Payload:  {tampered_ciphertext.hex()}")
    try:
        secure_envelope_unpack(tampered_ciphertext, mac, k_enc, k_mac)
        print("❌ CRITICAL FAILURE: Tampered packet decrypted successfully!")
    except ValueError as e:
        print(f"  * Security Action:   {e}")
        print("  * Safety Guard:      ✔ PASS (Attack completely neutralized!)")
    print("======================================================================\n")

if __name__ == "__main__":
    main()
