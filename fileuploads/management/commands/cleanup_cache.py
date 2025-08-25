from django.core.management.base import BaseCommand
from fileuploads.embeddings_service import embedder


class Command(BaseCommand):
    help = 'Limpia el cache de embeddings'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Fuerza la limpieza sin verificar condiciones',
        )
        parser.add_argument(
            '--stats',
            action='store_true',
            help='Solo muestra estadísticas del cache',
        )

    def handle(self, *args, **options):
        cache_stats = embedder.get_cache_stats()

        if options['stats']:
            self.stdout.write(
                self.style.SUCCESS(f'Cache Stats: {cache_stats}')
            )
            return

        if options['force']:
            embedder.clear_cache()
            self.stdout.write(
                self.style.SUCCESS('Cache limpiado forzosamente')
            )
        else:
            if embedder.should_cleanup_cache():
                cleanup_result = embedder.cleanup_cache()
                if cleanup_result:
                    self.stdout.write(
                        self.style.SUCCESS(f'Cache limpiado automáticamente. Stats antes: {cache_stats}')
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING('Error al limpiar cache')
                    )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'Cache no necesita limpieza. Stats: {cache_stats}'
                    )
                )