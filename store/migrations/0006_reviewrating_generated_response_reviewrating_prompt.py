# Generated by Django 4.2.6 on 2023-10-16 09:15

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0005_alter_reviewrating_review_generatedescription'),
    ]

    operations = [
        migrations.AddField(
            model_name='reviewrating',
            name='generated_response',
            field=models.TextField(blank=True, max_length=4000),
        ),
        migrations.AddField(
            model_name='reviewrating',
            name='prompt',
            field=models.TextField(blank=True, max_length=4000),
        ),
    ]
