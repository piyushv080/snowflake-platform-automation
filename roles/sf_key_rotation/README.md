# sf_key_rotation

Rotates a user's RSA key using Snowflake's two public key slots.

| Phase | What happens | Old key |
|-------|--------------|---------|
| `stage` | new key -> `RSA_PUBLIC_KEY_2`, test login with it (rolls back on failure) | still works |
| `promote` | new key -> `RSA_PUBLIC_KEY`, unset slot 2 | stops working |
| `full` | stage + promote | stops working |

Zero-downtime path: run `stage`, update Vault / AWX credential, then run `promote`.
Set `sf_key_rotation_update_awx: true` to update the AWX credential automatically (`awx.awx.credential`).
Does not support `--check`.
