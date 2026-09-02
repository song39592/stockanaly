import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const dir = dirname(fileURLToPath(import.meta.url));
const template = await readFile(join(dir, 'cordis.patch.yml'), 'utf8');
const pluginBase = pathToFileURL(join(dir, 'plugins')).href.replace(/\/$/, '');
await writeFile(join(dir, 'cordis.runtime.yml'), template.replaceAll('__PLUGIN_BASE__', pluginBase), 'utf8');
