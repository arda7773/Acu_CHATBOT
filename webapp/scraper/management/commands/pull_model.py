import json
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Pull the chat model and nomic-embed-text embedding model'

    def handle(self, *args, **options):
        # Wait for Ollama to be fully ready before pulling
        self.stdout.write('Waiting for Ollama to be ready...')
        for _ in range(30):
            try:
                r = requests.get(f'{settings.OLLAMA_URL}/api/tags', timeout=5)
                if r.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(3)

        for model in [settings.OLLAMA_MODEL, 'nomic-embed-text']:
            self._pull(model)

    def _pull(self, model: str):
        self.stdout.write(f'Pulling model: {model} ...')
        try:
            response = requests.post(
                f'{settings.OLLAMA_URL}/api/pull',
                json={'name': model},
                timeout=600,
                stream=True,
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line.decode())
                        status = data.get('status', '')
                        if 'total' in data and 'completed' in data:
                            pct = int(data['completed'] / data['total'] * 100)
                            self.stdout.write(f'\r  {status}: {pct}%', ending='')
                        else:
                            self.stdout.write(f'  {status}')
                    except json.JSONDecodeError:
                        pass

            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(f'Model {model} is ready!'))

        except requests.RequestException as e:
            self.stdout.write(self.style.ERROR(f'Failed to pull {model}: {e}'))
