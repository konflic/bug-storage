# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, report privately via GitHub's **"Report a vulnerability"** feature
(Security → Advisories → Report a vulnerability) on this repository, or email
the maintainer at `<security-contact@example.com>`.

Please include:
- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- affected version/commit.

You can expect an initial acknowledgement within a few business days. We'll work
with you on a fix and coordinate disclosure.

## Supported versions

This is a small project; only the latest `main` receives security fixes.

## Security notes for operators

The service ships with intentionally minimal security. When deploying it,
please be aware:

- **Authorization is a single shared secret** (`API_KEY`). There is no
  per-user identity or revocation — rotating the key invalidates all clients
  at once (`make rotate-key`). Treat the key like a password.
- **Always run behind HTTPS.** The provided Caddy config terminates TLS so the
  API key isn't transmitted in clear text. Do not expose port 8000 directly.
- **Auth is disabled when `API_KEY` is empty.** This is meant for local
  development only — never run an internet-facing instance without a key.
- **Secrets stay out of the repo.** `deploy/.env.prod`,
  `deploy/terraform/terraform.tfvars`, and Terraform state are git-ignored.
  Consider storing `API_KEY` in a secrets manager (e.g. YC Lockbox) for
  production.
- **Restrict SSH.** Lock `ssh_allowed_cidrs` (Terraform) to your own IP.
