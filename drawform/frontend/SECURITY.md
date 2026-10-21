# Security

The website is static. The optional LTX helper runs in a terminal and is never bundled into the site.

Store LTX credentials in the process environment or ignored `.env.ltx.local`. Never expose keys through Vite's `VITE_` variables, browser code or public media. If a key is disclosed, revoke it in the LTX console before creating a replacement.

Do not post secrets in issues. For a security vulnerability, use GitHub's private vulnerability reporting if enabled on this repository. Otherwise open an issue requesting a private contact without including exploit details or credentials.

No authentication, customer data storage, payment processing or production API gateway is included. Building these into a fork requires its own design and review.
