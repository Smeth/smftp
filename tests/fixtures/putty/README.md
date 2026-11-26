# Real PuTTY key files

Test keys written by PuTTYgen, from the test suite of the Ruby library
[putty-key](https://github.com/philr/putty-key) (MIT licence, see `LICENSE-putty-key.txt`).
Their passphrase is `Test Passphrase`. The `.pem` files hold the same keys in OpenSSL format:
`tests/test_ppk_real_files.py` checks that SMFTP reads exactly the same key from both.

These keys are public test material: never use them to protect anything.
