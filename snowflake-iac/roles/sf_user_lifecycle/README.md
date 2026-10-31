# sf_user_lifecycle

Joiner / mover / leaver sync from Active Directory to Snowflake.

1. `ansible.windows.win_powershell` runs `files/Get-ADGroupMembers.ps1` on the `windows_ad` host (WinRM)
   and returns enabled members of each mapped group with their target roles.
2. `snowflake_user` creates/updates users (`TYPE=PERSON`, comment `managed-by:ad-sync`) and aligns the **managed**
   roles (`roles_scope`) - roles granted by other processes are untouched.
3. Managed users no longer in any group are disabled and lose their managed roles.

Safety rails: fails if AD returns fewer than `sf_user_lifecycle_min_users` users, or if more than
`sf_user_lifecycle_max_leavers` users would be disabled.

Requirements: Windows host with RSAT ActiveDirectory module, WinRM over HTTPS, `pywinrm` in the EE.
PERSON users get no password - they sign in through SSO.
