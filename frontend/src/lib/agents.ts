import type { Member } from "./api";

export const sortMembers = (ms: Member[]): Member[] => [...ms].sort((a, b) => Number(a.available) - Number(b.available));
