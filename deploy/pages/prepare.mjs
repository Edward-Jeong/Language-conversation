/** Copy the reviewed Pages worker into the Vite output after building. */
import { copyFile, writeFile } from 'node:fs/promises';
await copyFile('deploy/pages/_worker.js', 'apps/web/dist/_worker.js');
await writeFile('apps/web/dist/_routes.json', JSON.stringify({version:1,include:['/*'],exclude:[]}));
await writeFile('apps/web/dist/robots.txt', 'User-agent: *\nDisallow: /\n');
console.log('Prepared Pages worker and private test crawler policy.');
