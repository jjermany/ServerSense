import { createContext, useContext } from "react";

export type TimeZoneContextValue = {
  timeZone: string;
  setTimeZone: (value: string) => void;
};

export const TimeZoneContext = createContext<TimeZoneContextValue>({
  timeZone: "UTC",
  setTimeZone: () => undefined,
});

export function useTimeZone() {
  return useContext(TimeZoneContext);
}
