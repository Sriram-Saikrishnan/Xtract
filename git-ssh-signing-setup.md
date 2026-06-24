# Git Commit Signing with SSH Keys on Windows

## Prerequisites

- Git 2.34+ (SSH signing support added in this version)
- OpenSSH client (built into Windows 10/11)

Check versions:
```powershell
git --version
ssh -V
```

---

## Step 1: Generate an SSH Key (skip if you already have one)

```powershell
ssh-keygen -t ed25519 -C "your_email@example.com"
```

- Accept the default path (`C:\Users\YourName\.ssh\id_ed25519`) or choose a custom path.
- Set a passphrase (recommended).

---

## Step 2: Add the Key to ssh-agent

Start the agent and add your key:

```powershell
# Start ssh-agent (run as Administrator if it fails)
Start-Service ssh-agent

# Set it to start automatically
Set-Service -Name ssh-agent -StartupType Automatic

# Add your key
ssh-add C:\Users\YourName\.ssh\id_ed25519
```

---

## Step 3: Configure Git to Sign Commits with SSH

```powershell
# Tell Git to use SSH for signing
git config --global gpg.format ssh

# Point Git to your public key
git config --global user.signingkey "C:/Users/YourName/.ssh/id_ed25519.pub"

# Sign all commits automatically (optional but recommended)
git config --global commit.gpgsign true
```

> Use forward slashes (`/`) in the key path, even on Windows.

---

## Step 4: Create an Allowed Signers File

Git needs this file to verify signatures. It maps email addresses to public keys.

```powershell
# Create the file
New-Item -ItemType Directory -Force "$HOME\.ssh" | Out-Null

# Get your public key content
$pubkey = Get-Content "$HOME\.ssh\id_ed25519.pub"

# Write the allowed_signers file (format: "email keytype key")
"your_email@example.com $pubkey" | Out-File -Encoding utf8 "$HOME\.ssh\allowed_signers"
```

Tell Git where this file is:

```powershell
git config --global gpg.ssh.allowedSignersFile "C:/Users/YourName/.ssh/allowed_signers"
```

---

## Step 5: Add the Public Key to GitHub

1. Copy your public key:
   ```powershell
   Get-Content "$HOME\.ssh\id_ed25519.pub" | clip
   ```
2. Go to **GitHub → Settings → SSH and GPG keys → New SSH key**.
3. Set **Key type** to **Signing Key**.
4. Paste and save.

---

## Step 6: Test It

Make a commit and verify the signature:

```powershell
git commit --allow-empty -m "test: verify SSH signing"
git log --show-signature -1
```

Expected output includes:
```
Good "git" signature for your_email@example.com with ED25519 key ...
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `error: unsupported value for gpg.format` | Upgrade Git to 2.34+ |
| `Permission denied` on ssh-agent | Run PowerShell as Administrator |
| `No such identity` | Run `ssh-add path\to\key` again |
| Signature shows `unknown` on GitHub | Make sure the key is added as a **Signing Key** (not just Auth key) |
| Path error in signingkey | Use forward slashes, not backslashes |

---

## Quick Reference — All Config Commands

```powershell
git config --global gpg.format ssh
git config --global user.signingkey "C:/Users/YourName/.ssh/id_ed25519.pub"
git config --global commit.gpgsign true
git config --global gpg.ssh.allowedSignersFile "C:/Users/YourName/.ssh/allowed_signers"
```
