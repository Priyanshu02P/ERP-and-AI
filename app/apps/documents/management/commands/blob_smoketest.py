
"""Run with: python manage.py blob_smoketest"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.documents.storage import get_backend


class Command(BaseCommand):
    help = "Upload, download, verify, and delete a test Blob."

    def handle(self, *args, **options):
        backend = get_backend()
        key = f"smoketest/{uuid.uuid4().hex}.txt"
        payload = b"procurement-blob-ok"

        self.stdout.write(
            f"Backend: {backend.__class__.__name__}"
        )

        blob = None

        try:
            # 1. Upload
            blob = backend.put(key, payload, "text/plain")
            self.stdout.write(f"PUT    OK: {blob.key}")
            self.stdout.write(f"URL: {blob.url}")

            # 2. Download and verify
            downloaded = b"".join(
                backend.open(blob.key, blob.url)
            )

            if downloaded != payload:
                raise CommandError(
                    "Downloaded content differs from uploaded content."
                )

            self.stdout.write("GET    OK: bytes match")

            # 3. Delete
            backend.delete(blob.key, blob.url)
            blob = None

            self.stdout.write("DELETE OK")

        except CommandError:
            raise

        except Exception as exc:
            raise CommandError(
                f"Blob smoke test failed: {type(exc).__name__}: {exc}"
            ) from exc

        finally:
            # Best-effort cleanup if upload succeeded but a later step failed.
            if blob is not None:
                try:
                    backend.delete(blob.key, blob.url)
                    self.stderr.write(
                        "Cleanup attempted for the test Blob."
                    )
                except Exception as cleanup_exc:
                    self.stderr.write(
                        f"WARNING: cleanup failed: {cleanup_exc}"
                    )

        self.stdout.write(
            self.style.SUCCESS("Blob storage smoke test passed.")
        )
