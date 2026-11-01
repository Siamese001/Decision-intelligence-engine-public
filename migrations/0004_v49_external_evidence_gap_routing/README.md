# NEE v49 external-evidence gap-routing migration

This approval-bound migration replaces an exact v48.2 baseline database (`b6b10223b7c6f25eed890df932f0dca50dc0ed47b5d57cb30f3f2f1df01f6ea6`) with the packaged v49 candidate (`fdd7015357f41bc8a3ee8424522f4199c95be9352b8df269c7af7e230a0fdb05`), validates the 13 sources and 18 candidate assertions, supports an idempotent forward pass, and restores the byte-exact v48.2 rollback snapshot.

It does not change schema, identities, validation states, canonical card bytes, or release promotion state.
