from django.db import migrations
from pgvector.django import HnswIndex


class Migration(migrations.Migration):

    dependencies = [
        ("scraper", "0002_contentchunk_metadata"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        'CREATE INDEX IF NOT EXISTS "contentchunk_embedding_idx" '
                        'ON "scraper_contentchunk" USING hnsw '
                        '("embedding" vector_cosine_ops) '
                        "WITH (m = 16, ef_construction = 64)"
                    ),
                    reverse_sql='DROP INDEX IF EXISTS "contentchunk_embedding_idx"',
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="contentchunk",
                    index=HnswIndex(
                        name="contentchunk_embedding_idx",
                        fields=["embedding"],
                        m=16,
                        ef_construction=64,
                        opclasses=["vector_cosine_ops"],
                    ),
                ),
            ],
        ),
    ]
