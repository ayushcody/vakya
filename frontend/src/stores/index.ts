import { create } from "zustand";
import type { Session, User } from "@supabase/supabase-js";

interface AuthState {
  user: User | null;
  session: Session | null;
  role: string;
  loading: boolean;
  setAuth: (session: Session | null) => void;
  setLoading: (l: boolean) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  session: null,
  role: "user",
  loading: true,
  setAuth: (session) =>
    set({
      session,
      user: session?.user ?? null,
      role: (session?.user?.app_metadata?.role as string) || "user",
      loading: false,
    }),
  setLoading: (loading) => set({ loading }),
}));
