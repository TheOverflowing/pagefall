// esbuild rewrites free fetch references in vendor modules without changing the host page's fetch.
export {safeFetch as fetch} from './shims/http';
export {quietConsole as console} from './shims/log';
