"""`python manage.py blob_smoketest` - upload, download and delete one tiny file against the
configured backend. Run it once against your real Vercel Blob store after setting the token."""
import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.documents.storage import get_backend


class Command(BaseCommand):
    help = "Round-trip test of the configured blob backend."

    def handle(self, *args, **options):
        backend, key, payload = get_backend(), f"smoketest/{uuid.uuid4().hex}.txt", b"procurement-blob-ok"
        self.stdout.write(f"Backend: {backend.__class__.__name__}")
        try:
            blob = backend.put(key, payload, "text/plain")
            self.stdout.write(f"  put    ok -> {blob.url}")
            data = b"".join(backend.open(blob.key, blob.url))
            if data != payload:
                raise CommandError("Downloaded bytes differ from uploaded bytes.")
            self.stdout.write("  get    ok")
            backend.delete(blob.key, blob.url)
            self.stdout.write("  delete ok")
        except CommandError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Blob smoke test failed: {exc}")
        self.stdout.write(self.style.SUCCESS("Blob storage works."))
