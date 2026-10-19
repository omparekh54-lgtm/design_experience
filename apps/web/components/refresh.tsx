"use client";
import {useState} from "react";
export function Refresh(){const[pending,setPending]=useState(false);return <button type="button" className="link-button secondary" disabled={pending} onClick={()=>{setPending(true);window.location.reload();}} aria-label="Refresh saved data">{pending?'Refreshing…':'↻ Refresh'}</button>;}
