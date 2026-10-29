import { afterEach, describe, expect, it } from "vitest";

import { forgetEveryKeptDraft, keptDraft, setDraftOwner } from "./useDraft";

const PATH = "/app/quality/alerts/new";

function keep(login: number, at: number) {
  window.localStorage.setItem(`erp.kept-draft:${login}:${PATH}`, JSON.stringify({ at, drafts: [{ title: "GSM low" }] }));
}

describe("a draft kept when the session ended (review F4)", () => {
  afterEach(() => { window.localStorage.clear(); setDraftOwner(null); });

  it("is offered to the login that typed it, and to nobody else", () => {
    keep(7, Date.now());
    expect(keptDraft(PATH, 7)?.drafts).toEqual([{ title: "GSM low" }]);
    expect(keptDraft(PATH, 8)).toBeNull();
    expect(keptDraft(PATH, null)).toBeNull();
  });

  it("lasts a day at most", () => {
    const now = Date.now();
    keep(7, now - 24 * 60 * 60 * 1000);
    expect(keptDraft(PATH, 7, now)).toBeNull();
    expect(window.localStorage.length).toBe(0);
  });

  it("is gone at sign-out, whoever typed it", () => {
    keep(7, Date.now());
    keep(8, Date.now());
    window.localStorage.setItem("other", "kept");
    forgetEveryKeptDraft();
    expect(Object.keys(window.localStorage)).toEqual(["other"]);
  });
});
