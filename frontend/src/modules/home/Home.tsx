import { Link } from "react-router";

import { MODULES, screenUrl } from "../../app/registry";
import { useAccess } from "../../auth/me";
import { Icon } from "../../shell/Icon";

function greeting(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

/** Where a person starts: the screens their roles open to them. */
export default function Home() {
  const { me, can } = useAccess();
  const modules = MODULES.map((module) => ({
    ...module,
    screens: module.screens.filter((screen) => can(screen.permission)),
  })).filter((module) => module.screens.length > 0);

  return (
    <section className="home">
      <h1>{greeting(new Date().getHours())}, {me.name.split(" ")[0]}</h1>
      {modules.length === 0 ? (
        <p className="muted">
          {me.roles.length === 0 && !me.is_superuser
            ? "Your account has no role yet, so there is nothing here to open. Ask your administrator to give you the role for your job."
            : `Nothing in this application is open to your role (${me.roles.join(", ")}) yet.`}
        </p>
      ) : (
        <div className="cards">
          {modules.map((module) => (
            <article key={module.key} className="card">
              <h2><Icon name={module.icon} /> {module.label}</h2>
              <ul>
                {module.screens.map((screen) => (
                  <li key={screen.path}><Link to={screenUrl(module, screen)}>{screen.label}</Link></li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
