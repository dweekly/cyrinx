package com.dweekly.cyrinxhil

import java.math.BigInteger
import java.security.SecureRandom

object X25519 {
    private val P = BigInteger.valueOf(2).pow(255).subtract(BigInteger.valueOf(19))

    fun generatePrivateKey(): ByteArray {
        val random = SecureRandom()
        val key = ByteArray(32)
        random.nextBytes(key)
        return clamp(key)
    }

    fun clamp(key: ByteArray): ByteArray {
        val clamped = key.clone()
        clamped[0] = (clamped[0].toInt() and 248).toByte()
        clamped[31] = (clamped[31].toInt() and 127).toByte()
        clamped[31] = (clamped[31].toInt() or 64).toByte()
        return clamped
    }

    fun scalarMult(scalar: ByteArray, u: ByteArray): ByteArray {
        val k = bytesLEToBigInt(scalar)
        val uVal = bytesLEToBigInt(u)
        val result = ladder(k, uVal)
        return bigIntToBytesLE(result)
    }

    fun getPublicKey(privateKey: ByteArray): ByteArray {
        val base = ByteArray(32)
        base[0] = 9
        return scalarMult(privateKey, base)
    }

    private fun bytesLEToBigInt(bytes: ByteArray): BigInteger {
        val bigEndian = ByteArray(bytes.size)
        for (i in bytes.indices) {
            bigEndian[i] = bytes[bytes.size - 1 - i]
        }
        return BigInteger(1, bigEndian)
    }

    private fun bigIntToBytesLE(bigInt: BigInteger): ByteArray {
        val raw = bigInt.toByteArray()
        val le = ByteArray(32)
        var startIdx = 0
        if (raw.size > 1 && raw[0] == 0.toByte()) {
            startIdx = 1
        }
        val len = raw.size - startIdx
        for (i in 0 until minOf(32, len)) {
            le[i] = raw[raw.size - 1 - i]
        }
        return le
    }

    private fun ladder(k: BigInteger, u: BigInteger): BigInteger {
        var x1 = u
        var x2 = BigInteger.ONE
        var z2 = BigInteger.ZERO
        var x3 = u
        var z3 = BigInteger.ONE

        for (t in 254 downTo 0) {
            val kt = if (k.testBit(t)) 1 else 0
            if (kt == 1) {
                var temp = x2; x2 = x3; x3 = temp
                temp = z2; z2 = z3; z3 = temp
            }

            val A = x2.add(z2).mod(P)
            val AA = A.multiply(A).mod(P)
            val B = x2.subtract(z2).mod(P)
            val BB = B.multiply(B).mod(P)
            val C = x3.add(z3).mod(P)
            val D = x3.subtract(z3).mod(P)
            val DA = D.multiply(A).mod(P)
            val CB = C.multiply(B).mod(P)

            x3 = DA.add(CB).pow(2).mod(P)
            z3 = u.multiply(DA.subtract(CB).pow(2)).mod(P)
            x2 = AA.multiply(BB).mod(P)
            val E = AA.subtract(BB).mod(P)
            z2 = E.multiply(BB.add(BigInteger.valueOf(121665).multiply(E))).mod(P)

            if (kt == 1) {
                var temp = x2; x2 = x3; x3 = temp
                temp = z2; z2 = z3; z3 = temp
            }
        }

        return x2.multiply(z2.modInverse(P)).mod(P)
    }
}
