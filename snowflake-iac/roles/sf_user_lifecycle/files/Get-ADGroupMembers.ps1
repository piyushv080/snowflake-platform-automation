<#
.SYNOPSIS
    Returns enabled AD users of the mapped groups with their target Snowflake roles.

.DESCRIPTION
    Executed by ansible.windows.win_powershell on the Windows management host
    (role sf_user_lifecycle). Requires the ActiveDirectory module (RSAT).
    Output is returned through $Ansible.Result as a list of:
      @{ name; login_name; display_name; email; roles = @(...) }
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $MappingJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory

$mappings = $MappingJson | ConvertFrom-Json
$users = @{}

foreach ($mapping in $mappings) {
    $members = Get-ADGroupMember -Identity $mapping.ad_group -Recursive |
        Where-Object { $_.objectClass -eq 'user' }

    foreach ($member in $members) {
        $ad = Get-ADUser -Identity $member.SID -Properties mail, displayName, userPrincipalName, enabled
        if (-not $ad.Enabled) { continue }

        # Snowflake identifier: letters, digits, underscore; must not start with a digit.
        $name = ($ad.SamAccountName -replace '[^A-Za-z0-9_]', '_').ToUpperInvariant()
        if ($name -notmatch '^[A-Z_][A-Z0-9_]*$') {
            Write-Warning "Skipping $($ad.SamAccountName): not a valid Snowflake identifier"
            continue
        }

        if (-not $users.ContainsKey($name)) {
            $users[$name] = [pscustomobject]@{
                name         = $name
                login_name   = $ad.UserPrincipalName
                display_name = $ad.DisplayName
                email        = $ad.mail
                roles        = [System.Collections.Generic.List[string]]::new()
            }
        }
        $role = $mapping.snowflake_role.ToUpperInvariant()
        if (-not $users[$name].roles.Contains($role)) { $users[$name].roles.Add($role) }
    }
}

$Ansible.Changed = $false
$Ansible.Result = @(
    $users.Values | Sort-Object name | ForEach-Object {
        [pscustomobject]@{
            name         = $_.name
            login_name   = $_.login_name
            display_name = $_.display_name
            email        = $_.email
            roles        = @($_.roles)
        }
    }
)
