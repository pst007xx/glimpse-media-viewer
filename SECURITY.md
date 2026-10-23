# Security Policy

## Supported Versions

Security fixes go into the latest release of Glimpse Media Viewer. Please update
to it before reporting, as the problem may already be fixed.

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

The Snap Store build (`snap install glimpse-media-viewer`) is updated
automatically, so most people are always on the latest version.

## Reporting a Vulnerability

Please **don't** report security problems in public issues.

Report them privately instead, on GitHub:

1. Go to the **Security** tab of this repository.
2. Choose **Report a vulnerability**.
3. Describe the problem: what you did, what happened, the version of Glimpse
   (shown in About), how it was installed (Snap or from source) and, if you can,
   a file or the steps that show the problem.

Only you and the maintainer can see the report.

### What to expect

- You'll get a reply within **7 days** saying whether the report has been
  received and confirmed, or if more information is needed.
- If it's confirmed, a fix will be worked on and released as soon as practical,
  usually within **30 days**, depending on how serious it is. You'll be kept up
  to date along the way.
- Once the fix is released, the problem will be published as a security
  advisory, crediting you unless you'd rather not be named.
- If it turns out not to be a security problem, you'll be told why, and it may
  be moved to a normal issue.

Glimpse is maintained by one person in their spare time, so please be patient.

### What's covered

- Glimpse's own code (`glimpse-media-viewer/glimpse.py`) and its Snap packaging
  (`snap/snapcraft.yaml`), for example a file that makes Glimpse damage, delete
  or overwrite other files, or a way around the Snap's confinement.

Problems in the libraries Glimpse uses, such as GTK, GStreamer, gdk-pixbuf or
exiv2, should be reported to those projects. If you're not sure where a problem
belongs, report it here and it will be passed on.
