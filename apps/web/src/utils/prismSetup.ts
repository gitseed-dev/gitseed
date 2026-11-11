import { Prism } from 'prism-react-renderer'

// Expose Prism on globalThis so additional prismjs language components can register on it
;(globalThis as unknown as { Prism: typeof Prism }).Prism = Prism

export { Prism }

