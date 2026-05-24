import hashlib
import random

def compute_sac(alice_pub, bob_pub, shared_secret):
    """
    Computes a 4-digit Short Authentication Code (SAC)
    based on the commitment of public keys and the derived secret.
    SAC = SHA-256(alice_pub || bob_pub || shared_secret) mod 10000
    """
    hasher = hashlib.sha256()
    hasher.update(alice_pub.to_bytes(32, 'big'))
    hasher.update(bob_pub.to_bytes(32, 'big'))
    hasher.update(shared_secret.to_bytes(32, 'big'))
    digest = hasher.digest()
    
    # Extract last 4 bytes as integer and mod 10000 to get a 4-digit pin
    pin_val = int.from_bytes(digest[-4:], 'big') % 10000
    return pin_val

def map_pin_to_acoustic_melody(pin_val):
    """
    Maps a 4-digit PIN to a sequence of 4 distinct ultrasonic frequencies 
    (frequencies between 18.5 kHz and 20.5 kHz) representing an acoustic signature.
    """
    freqs = []
    digits = f"{pin_val:04d}"
    for d in digits:
        val = int(d)
        # Map 0-9 to frequencies
        f = 18500.0 + val * 200.0
        freqs.append(f)
    return freqs

def main():
    print("======================================================================")
    print("      🧪 CRITIQUE FOLLOW-UP: ACOUSTIC MitM & SAC MITIGATION 🧪")
    print("======================================================================")
    print("\nCritique: Acoustic ECDH is highly vulnerable to active Man-in-the-Middle")
    print("(MitM) attacks. An eavesdropper can intercept public keys and inject their own,")
    print("completely decrypting the traffic without either side knowing.")
    
    # Mock normal values
    alice_pub = 0x5a09ff4c79a55c102a03bc29c66a4f1074de24c9657b05090551e8d1314db1cf
    bob_pub = 0x61ddadcab6d5f307137fbc2005a3b3420a8dbd61ef3437e01a3f3437a091cf8e
    
    # 1. Normal/Secure Exchange Case
    print("\n🟢 CASE 1: SECURE CRYPTOGRAPHIC PAIRING (NO MITM):")
    print("----------------------------------------------------------------------")
    normal_shared = 0x66635448119a3ec7d5d58dc018ff9d3a771ad2b350da2724461b69b1056eca0c
    
    sac_alice = compute_sac(alice_pub, bob_pub, normal_shared)
    sac_bob = compute_sac(alice_pub, bob_pub, normal_shared)
    melody_alice = map_pin_to_acoustic_melody(sac_alice)
    
    print(f"  * Alice Public Commitment PIN:  {sac_alice:04d}")
    print(f"  * Bob Public Commitment PIN:    {sac_bob:04d}")
    print(f"  * Alice Acoustic Signature:     {['{:.1f} kHz'.format(f/1000) for f in melody_alice]}")
    
    assert sac_alice == sac_bob
    print("  * Pairing Status:               ✔ MATCH SUCCESS (Session Secure!)")
    print("----------------------------------------------------------------------")
    
    # 2. MitM Interception Case
    # An attacker (Mallory) sits between Alice and Bob:
    # Alice sends alice_pub -> Mallory intercepts, sends mallory_alice_pub to Bob
    # Bob sends bob_pub -> Mallory intercepts, sends mallory_bob_pub to Alice
    print("\n🔴 CASE 2: ACTIVE MAN-IN-THE-MIDDLE ATTACK (MALLORY INJECTS KEYS):")
    print("----------------------------------------------------------------------")
    mallory_pub = 0x9999999999999999999999999999999999999999999999999999999999999999
    
    # Alice derives a secret with Mallory
    alice_mitm_shared = 0x1111111111111111111111111111111111111111111111111111111111111111
    # Bob derives a secret with Mallory
    bob_mitm_shared = 0x2222222222222222222222222222222222222222222222222222222222222222
    
    # Alice computes SAC assuming she is connected to Bob
    sac_alice_mitm = compute_sac(alice_pub, mallory_pub, alice_mitm_shared)
    # Bob computes SAC assuming he is connected to Alice
    sac_bob_mitm = compute_sac(mallory_pub, bob_pub, bob_mitm_shared)
    
    melody_alice_mitm = map_pin_to_acoustic_melody(sac_alice_mitm)
    melody_bob_mitm = map_pin_to_acoustic_melody(sac_bob_mitm)
    
    print(f"  * Alice Thinks Bob's PIN is:    {sac_alice_mitm:04d}")
    print(f"  * Bob Thinks Alice's PIN is:    {sac_bob_mitm:04d}")
    print(f"  * Alice Acoustic Melody Played: {['{:.1f} kHz'.format(f/1000) for f in melody_alice_mitm]}")
    print(f"  * Bob Acoustic Melody Played:   {['{:.1f} kHz'.format(f/1000) for f in melody_bob_mitm]}")
    
    if sac_alice_mitm != sac_bob_mitm:
        print("  * Pairing Status:               ❌ PIN MISMATCH!")
        print("  * Security Action:              🚨 ATTACK DETECTED! Terminating connection immediately.")
    else:
        print("  * Pairing Status:               ✔ MATCH SUCCESS (Failure!)")
    print("----------------------------------------------------------------------")
    
    print("\n💡 RESPONSIVE ARCHITECTURAL RECOMMENDATION:")
    print("1. Pair acoustic ECDH with a 4-digit Short Authentication Code (SAC).")
    print("2. The SAC can be cross-verified visually by the user, or played acoustically")
    print("   as a fast 4-tone chord signature melody that both devices listen to and")
    print("   match automatically, proving mathematical agreement with zero manual UX.")
    print("======================================================================\n")

if __name__ == "__main__":
    main()
