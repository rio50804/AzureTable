import os
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, abort, send_file
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.core.exceptions import ResourceNotFoundError
from PIL import Image, UnidentifiedImageError

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 6 * 1024 * 1024
connection = os.environ['AZURE_STORAGE_CONNECTION_STRING']
table = TableServiceClient.from_connection_string(connection).create_table_if_not_exists('posts')
container = BlobServiceClient.from_connection_string(connection).get_container_client('images')
JST = timezone(timedelta(hours=9))
CATEGORIES = ['勉強', '部活動', '進路', '学校生活', '雑談']

@app.route('/')
def index():
    category = request.args.get('category', '')
    messages = list(table.list_entities())
    if category in CATEGORIES:
        messages = [m for m in messages if m.get('category', '雑談') == category]
    messages.sort(key=lambda m: m.get('posted_at', ''), reverse=True)
    return render_template('index.html', messages=messages, categories=CATEGORIES, selected=category)

@app.route('/post', methods=['POST'])
def post():
    name = request.form.get('name', '').strip() or '名無し'
    text = request.form.get('message', '').strip()
    category = request.form.get('category', '雑談')
    if not text or len(text) > 500 or len(name) > 30 or category not in CATEGORIES:
        abort(400, '入力内容を確認してください。')
    row = {'PartitionKey': 'board', 'RowKey': str(uuid.uuid4()), 'name': name,
           'text': text, 'category': category, 'posted_at': datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')}
    upload = request.files.get('image')
    if upload and upload.filename:
        data = upload.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            abort(413)
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format not in ('JPEG', 'PNG') or image.width * image.height > 12000000:
                    abort(400, 'JPEGまたはPNGの画像を選んでください。')
                image.load()
                image.thumbnail((1600, 1600))
                output = BytesIO()
                image.convert('RGB').save(output, format='JPEG', quality=85)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            abort(400, '画像を読み込めませんでした。')
        blob_name = row['RowKey'] + '.jpg'
        container.upload_blob(blob_name, output.getvalue(), content_settings=ContentSettings(content_type='image/jpeg'))
        row['image'] = blob_name
    try:
        table.create_entity(row)
    except Exception:
        if row.get('image'):
            try:
                container.delete_blob(row['image'])
            except Exception:
                app.logger.exception('Image cleanup failed')
        raise
    return redirect(url_for('index'))

@app.route('/image/<uuid:image_id>')
def image(image_id):
    try:
        row = table.get_entity('board', str(image_id))
        if row.get('image') != str(image_id) + '.jpg':
            abort(404)
        data = container.download_blob(row['image']).readall()
    except ResourceNotFoundError:
        abort(404)
    return send_file(BytesIO(data), mimetype='image/jpeg', max_age=300)

if __name__ == '__main__':
    app.run()
