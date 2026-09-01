import { ReactNode, useEffect, useState } from "react";
import { api } from "./api";
import { TimeZoneContext } from "./timeZoneContext";

export function TimeZoneProvider({ children }: { children: ReactNode }) {
  const [timeZone, setTimeZone] = useState("UTC");
  useEffect(() => {
    api<{ timezone?: string }>("/api/settings/general")
      .then((settings) => setTimeZone(settings.timezone || "UTC"))
      .catch(() => setTimeZone("UTC"));
  }, []);
  return (
    <TimeZoneContext.Provider value={{ timeZone, setTimeZone }}>
      {children}
    </TimeZoneContext.Provider>
  );
}
