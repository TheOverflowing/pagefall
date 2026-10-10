export default function pLimit(_count:number){let last=Promise.resolve();return (fn:any)=>{const next=last.then(fn);last=next.catch(()=>{});return next;};}
