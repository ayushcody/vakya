# Revocable provenance: threat model (22 October 2026)

One page on what a Vakya receipt proves, what withdrawing consent changes, and what it cannot do. It is the
basis for the `/verify` page and for what Vakya says in public about consent and provenance. Not legal advice.

## The claim

Every clip Vakya makes has a file label, a C2PA content manifest (when switched on) and a receipt
(`services/provenance.py`) signed with HMAC and Ed25519 that names the engine, the voice and the consent record behind it. Verification is
**revocable**: when the voice owner withdraws consent, the receipt is not changed (it is an audit trail), but the
withdrawal is appended to a hash-chained, Ed25519-signed log, and `/verify` reads the clip differently from then on.
In the competitor review of 22 October 2026 no product was found that links withdrawal to verification; that is an absence of evidence, not proof.

## What a verified clip reads as (the states)

`voice_consent_status` on every public receipt (`provenance.consent_status`):

| State | Meaning | How `/verify` shows it |
| --- | --- | --- |
| `active` | A cloned voice; consent was verified when the clip was made and has not been withdrawn | "Consent verified" |
| `withdrawn` | The voice owner has since withdrawn consent (`withdrawn_at` on the receipt) | "Consent verified when made, now withdrawn"; "Voice withdrawn" |
| `retention_ended` | The voice was deleted when its retention period ended | as withdrawn, with the reason |
| `unverified` | A cloned voice whose consent record exists but was not verified | "Consent recorded, not verified" |
| `preset_voice` | A built-in voice: nobody's voice was cloned, so no consent applies | no consent row |

Clip verdicts: `made_with_vakya` (the exact file hash leads to a receipt whose signature checks) and `not_detected`
(no matching receipt: the clip may come from elsewhere, or have been edited or re-encoded since it was made).

## Assets and adversaries

| Asset | Who wants to break it | How |
| --- | --- | --- |
| "This clip is AI-made" | A user passing a clip off as human | Strip the label and manifest, or re-encode the file: the receipt then no longer matches it byte for byte |
| "This clip was consented" | Someone cloning a voice without consent | Pass the consent gate with a zero-shot clone reading the code (not yet tested, see below) |
| "This clip verifies as withdrawn" | A user re-using a clip after withdrawal | Keep the clip; the receipt still reads as made-while-consented, and the withdrawal flag is what changes |
| The withdrawal log | An operator hiding a withdrawal | Delete or edit a row; the hash chain and signatures make this detectable, not impossible |
| The signing key | Theft, or rotation without `kid` | Rotation exists (`backend/scripts/rotate_receipt_key.py`; receipts carry `key_id`), but the offline SDKs take one public key at a time |

## What revocation does and does not do

Does: delete the voice's files (sealed by the vault; the key is destroyed), its embedding and prompt caches; append a
signed row to the public log; make every existing receipt for that voice read as withdrawn on `/verify`, within one
request of the write (no caching layer in between).

Does not: recall clips already copied elsewhere; strip the label from them (an unchanged copy still verifies as AI-made,
which is the point); change the receipt's signed fields; stop an operator with the machine and the model from
generating more (see `docs/VOICE_VAULT.md`).

## Evaluation the claim still needs

1. Revocation latency: withdraw, then `GET /api/receipts/{id}`; target under 60 s, measured today as one request.
2. How a C2PA reader treats the manifest beside the signed receipt (`bench/c2pa_spike.py`) and whether it shows the withdrawal.
3. The consent gate's false accept against Gnani's cloning, since "consented" is only as strong as the gate.
