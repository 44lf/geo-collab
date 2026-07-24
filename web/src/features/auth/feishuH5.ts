/**
 * 飞书 H5 JSSDK 桥接 helper。
 *
 * 仅在页面被飞书客户端内置浏览器打开时，`window.h5sdk` / `window.tt` 才存在
 * （由 web/index.html 引入的官方 JSSDK 注入）。普通浏览器打开时这两个全局
 * 变量不存在，所有 helper 安全短路返回 null/false，不影响非飞书场景。
 *
 * NOTE: `tt.requestAccess` 的精确参数（appId/scopeList 等）取自飞书官方文档，
 * 未在真实飞书客户端设备上验证过，上线前需要真机联调确认。
 */

declare global {
  interface Window {
    h5sdk?: {
      ready: (cb: () => void) => void;
      error?: (cb: (err: unknown) => void) => void;
    };
    tt?: {
      requestAccess: (options: {
        appId?: string;
        scopeList?: string[];
        success?: (res: { code?: string }) => void;
        fail?: (err: unknown) => void;
      }) => void;
    };
  }
}

/** 是否运行在飞书客户端内置浏览器里。 */
export function inFeishu(): boolean {
  return typeof window.h5sdk !== "undefined";
}

/**
 * 通过飞书 JSSDK 换取免登 code。非飞书环境或 SDK 未就绪时返回 null，
 * 不抛异常——调用方应把 null 当作“拿不到 code，走原有逻辑”处理。
 *
 * 桥接可能永远不回调（native bridge 卡死/异常机型），因此用 `timeoutMs`
 * 兜底：超时未拿到 code 就 resolve(null)，退化为走原有的手动登录逻辑，
 * 避免调用方（AuthContext）的 loading 状态被永久挂起。
 */
export function getFeishuAuthCode(timeoutMs = 5000): Promise<string | null> {
  const w = window;
  if (!w.h5sdk || !w.tt) return Promise.resolve(null);
  return new Promise((resolve) => {
    let settled = false;
    const done = (v: string | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(v);
    };
    const timer = setTimeout(() => done(null), timeoutMs);
    try {
      w.h5sdk!.ready(() => {
        w.tt!.requestAccess({
          // scopeList 留空表示只做免登鉴权，不申请额外用户信息权限。
          scopeList: [],
          success: (res) => done(res?.code ?? null),
          fail: () => done(null),
        });
      });
      w.h5sdk!.error?.(() => done(null));
    } catch {
      done(null);
    }
  });
}
