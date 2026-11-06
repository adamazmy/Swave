"use client";

import dynamic from "next/dynamic";
import { Loading } from "./night";

// Browser-only: the page lists this browser's localStorage keys on its first render.
const DataScreen = dynamic(() => import("./data-screen"), { ssr: false, loading: () => <Loading /> });

export default function DataRoute() {
  return <DataScreen />;
}
