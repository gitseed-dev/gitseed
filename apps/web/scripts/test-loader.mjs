import { existsSync, readFileSync, statSync } from 'node:fs'
import { register } from 'node:module'
import { dirname, resolve as resolvePath } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { isMainThread } from 'node:worker_threads'

if (isMainThread) {
  register(import.meta.url)
}

export async function resolve(specifier, context, nextResolve) {
  if (specifier.endsWith('.css')) {
    return {
      shortCircuit: true,
      url: 'data:text/javascript,export default {}',
    }
  }
  try {
    return await nextResolve(specifier, context)
  } catch (err) {
    if (
      err &&
      (err.code === 'ERR_MODULE_NOT_FOUND' ||
        err.code === 'ERR_UNSUPPORTED_DIR_IMPORT')
    ) {
      if (specifier.startsWith('./') || specifier.startsWith('../')) {
        const parentDir = context.parentURL
          ? dirname(fileURLToPath(context.parentURL))
          : process.cwd()
        const absTarget = resolvePath(parentDir, specifier)
        if (existsSync(`${absTarget}.js`)) {
          return nextResolve(pathToFileURL(`${absTarget}.js`).href, context)
        }
        if (
          existsSync(absTarget) &&
          statSync(absTarget).isDirectory() &&
          existsSync(resolvePath(absTarget, 'index.js'))
        ) {
          return nextResolve(
            pathToFileURL(resolvePath(absTarget, 'index.js')).href,
            context
          )
        }
      } else if (!specifier.endsWith('.js') && !specifier.startsWith('node:')) {
        try {
          return await nextResolve(`${specifier}.js`, context)
        } catch {
          // fall through to original error
        }
      }
    }
    throw err
  }
}

export async function load(url, context, nextLoad) {
  if (url.startsWith('file://') && url.endsWith('.json')) {
    const raw = readFileSync(fileURLToPath(url), 'utf8')
    return {
      format: 'module',
      shortCircuit: true,
      source: `export default ${raw}`,
    }
  }
  return nextLoad(url, context)
}
