"""Property-based test for at-rest encryption of API keys using Fernet (R22.2).

# Feature: multi-user-rag-platform, Property 52: Key encryption is a round-trip and
# never stores plaintext — for every arbitrary plaintext string (including unicode,
# whitespace, very long keys): (1) decryptSecret(encryptSecret(x)) == x (deterministic
# round-trip); (2) for sufficiently long plaintext (>= 8 bytes), the ciphertext bytes
# do NOT contain the utf-8 byte sequence of the plaintext (plaintext is not stored) —
# we skip very short strings because a Fernet token is random urlsafe-base64, so a few
# short bytes (e.g. "0") may match by chance, making the "does not contain" check
# meaningless; (3) encrypting the same plaintext twice still decrypts back to the
# correct plaintext (Fernet adds a timestamp/nonce, so the two ciphertexts differ but
# are both valid).
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.auth.crypto import decryptSecret, encryptSecret

# Arbitrary plaintext string: covers unicode (Vietnamese, multi-byte characters),
# whitespace, and very long keys (up to 500 characters). No min_size limit so that
# the empty string is also covered.
_plaintext = st.text(max_size=500)


@settings(max_examples=200, deadline=None)
@given(plaintext=_plaintext)
def test_ma_hoa_khoa_round_trip_va_khong_luu_plaintext(plaintext):
    ciphertext = encryptSecret(plaintext)

    # (1) Deterministic round-trip: decrypts back to the original plaintext.
    assert decryptSecret(ciphertext) == plaintext

    # (2) The ciphertext does NOT contain the utf-8 byte sequence of the plaintext (plaintext not stored).
    # Only checked for sufficiently long plaintext (>= 8 bytes): a Fernet token is
    # random urlsafe-base64, so a few short bytes (e.g. b"0") may match by chance
    # inside the token, making the "does not contain" check unreliable. For realistic
    # API key lengths the chance of a base64 collision is tiny, so the check stays meaningful.
    plaintextBytes = plaintext.encode("utf-8")
    if len(plaintextBytes) >= 8:
        assert plaintextBytes not in ciphertext

    # (3) Second encryption: Fernet adds a timestamp/nonce so the ciphertext differs,
    # but both decrypt back to the correct plaintext.
    ciphertextLan2 = encryptSecret(plaintext)
    assert decryptSecret(ciphertextLan2) == plaintext
