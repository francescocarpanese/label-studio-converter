import io
import os
import xml.etree.ElementTree
import requests
import hashlib
import logging
import urllib
import numpy as np
import wave
import shutil

from operator import itemgetter
from PIL import Image
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


def tokenize(text):
    tok_start = 0
    out = []
    for tok in text.split():
        if len(tok):
            out.append((tok, tok_start))
            tok_start += len(tok) + 1
        else:
            tok_start += 1
    return out


def create_tokens_and_tags(text, spans):
    tokens_and_idx = tokenize(text)
    if spans:
        spans = list(sorted(spans, key=itemgetter('start')))
        span = spans.pop(0)
        prefix = 'B-'
        tokens, tags = [], []
        for token, token_start in tokens_and_idx:
            tokens.append(token)
            token_end = token_start + len(token) - 1
            if not span or token_end < span['start']:
                tags.append('O')
            elif token_start >= span['end']:
                # this could happen if prev label ends with whitespaces, e.g. "cat " "too"
                # TODO: it is not right choice to place empty tag here in case when current token is covered by next span  # noqa
                tags.append('O')
            else:
                tags.append(prefix + span['labels'][0])
                if (span['end'] - 1) > token_end:
                    prefix = 'I-'
                elif len(spans):
                    span = spans.pop(0)
                    prefix = 'B-'
                else:
                    span = None
    else:
        tokens = [token for token, _ in tokens_and_idx]
        tags = ['O'] * len(tokens)

    return tokens, tags


def _get_upload_dir(project_dir):
    upload_dir = os.environ.get('LS_UPLOAD_DIR')
    if not upload_dir and project_dir:
        upload_dir = os.path.join(project_dir, 'upload')
        if not os.path.exists(upload_dir):
            upload_dir = None
    if not upload_dir:
        raise FileNotFoundError("Can't find upload dir: either LS_UPLOAD_DIR or project should be passed to converter")
    return upload_dir


def download(url, output_dir, filename=None, project_dir=None, return_relative_path=False):
    is_local_file = url.startswith('/data/') and '?d=' in url
    is_uploaded_file = url.startswith('/data/upload')

    if is_uploaded_file:
        upload_dir = _get_upload_dir(project_dir)
        filename = url.replace('/data/upload/', '')
        filepath = os.path.join(upload_dir, filename)
        logger.debug('Copy {filepath} to {output_dir}'.format(filepath=filepath, output_dir=output_dir))
        shutil.copy(filepath, output_dir)
        if return_relative_path:
            return os.path.join(os.path.basename(output_dir), filename)
        return filepath

    if is_local_file:
        filename, dir_path = url.split('/data/', 1)[-1].split('?d=')
        dir_path = str(urllib.parse.unquote(dir_path))
        if not os.path.exists(dir_path):
            raise FileNotFoundError(dir_path)
        filepath = os.path.join(dir_path, filename)
        if return_relative_path:
            raise NotImplementedError()
        return filepath

    if filename is None:
        basename, ext = os.path.splitext(os.path.basename(urlparse(url).path))
        filename = basename + '_' + hashlib.md5(url.encode()).hexdigest()[:4] + ext
    filepath = os.path.join(output_dir, filename)
    if not os.path.exists(filepath):
        logger.info('Download {url} to {filepath}'.format(url=url, filepath=filepath))
        r = requests.get(url)
        r.raise_for_status()
        with io.open(filepath, mode='wb') as fout:
            fout.write(r.content)
    if return_relative_path:
        return os.path.join(os.path.basename(output_dir), filename)
    return filepath


def get_image_size(image_path):
    return Image.open(image_path).size


def get_image_size_and_channels(image_path):
    i = Image.open(image_path)
    w, h = i.size
    c = len(i.getbands())
    return w, h, c


def get_audio_duration(audio_path):
    with wave.open(audio_path, mode='r') as f:
        return f.getnframes() / float(f.getframerate())


def ensure_dir(dir_path):
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)


def parse_config(config_string):

    def _is_input_tag(tag):
        return tag.attrib.get('name') and tag.attrib.get('value', '').startswith('$')

    def _is_output_tag(tag):
        return tag.attrib.get('name') and tag.attrib.get('toName')

    xml_tree = xml.etree.ElementTree.fromstring(config_string)

    inputs, outputs = {}, {}
    for tag in xml_tree.iter():
        if _is_input_tag(tag):
            inputs[tag.attrib['name']] = {'type': tag.tag, 'value': tag.attrib['value'].lstrip('$')}
        elif _is_output_tag(tag):
            outputs[tag.attrib['name']] = {'type': tag.tag, 'to_name': tag.attrib['toName'].split(',')}

    for output_tag, tag_info in outputs.items():
        tag_info['inputs'] = []
        for input_tag_name in tag_info['to_name']:
            if input_tag_name not in inputs:
                raise KeyError(
                    'to_name={input_tag_name} is specified for output tag name={output_tag}, but we can\'t find it '
                    'among input tags'.format(input_tag_name=input_tag_name, output_tag=output_tag))
            tag_info['inputs'].append(inputs[input_tag_name])

    return outputs


def get_polygon_area(x, y):
    """https://en.wikipedia.org/wiki/Shoelace_formula"""

    assert len(x) == len(y)

    return float(0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))


def get_polygon_bounding_box(x, y):
    assert len(x) == len(y)

    x1, y1, x2, y2 = min(x), min(y), max(x), max(y)
    return [x1, y1, x2 - x1, y2 - y1]
