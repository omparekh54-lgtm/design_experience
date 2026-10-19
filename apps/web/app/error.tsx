"use client";
export default function ErrorPage({reset}:{error:Error;reset:()=>void}){return <div className="card" role="alert"><h1>This view could not be loaded</h1><p className="subtle">Your account has not been changed. Try loading this page again.</p><button onClick={reset} className="link-button">Try again</button></div>;}
