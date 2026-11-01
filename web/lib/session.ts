// One session id per browser tab, so events from one seed → results run can be grouped.

const KEY = "swave-session";

export function newSession(): string {
  const id = crypto.randomUUID();
  try {
    sessionStorage.setItem(KEY, id);
  } catch {
    /* storage blocked: events just won't share an id */
  }
  return id;
}

export function currentSession(): string {
  try {
    return sessionStorage.getItem(KEY) ?? newSession();
  } catch {
    return crypto.randomUUID();
  }
}
