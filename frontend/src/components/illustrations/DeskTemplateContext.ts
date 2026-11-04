import { createContext, useContext } from "react";
import type { AgentTemplate } from "@/types/api";

/** The template the open agent came from (its persona chips). The front-desk page provides it; without one the header just omits the chips. */
export const DeskTemplateContext = createContext<AgentTemplate | null>(null);
export const useDeskTemplate = () => useContext(DeskTemplateContext);
