// Site responses, URLs and account data must never be logged by vendor parsers.
const noop=(..._args:any[])=>{};
export const log={debug:noop,info:noop,warn:noop,error:noop,trace:noop,log:noop};
export const saveLogTextToFile=noop;

export const quietConsole={...log,table:noop,group:noop,groupCollapsed:noop,groupEnd:noop,time:noop,timeEnd:noop,assert:noop};
