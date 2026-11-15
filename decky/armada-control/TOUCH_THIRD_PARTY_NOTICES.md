# Third-party notices

The Freedom Touch touch controller is integrated into the Freedom Control Decky plugin as its touch backend. It contains original work by danyi and the ArmadaOS Freedom contributors, and adaptations of the following projects. The integrated runtime keeps the original author and source; the plugin around it is licensed under GPL-2.0.

- **Decky Plugin Template**, SteamDeckHomebrew contributors: project scaffolding and build configuration, BSD-3-Clause. The original license text is retained in `licenses/freedom-touch/Decky-template-BSD-3-Clause.txt`.
- **Decky API and UI**, SteamDeckHomebrew contributors: frontend integration libraries under LGPL-2.1; license texts are retained in `licenses/freedom-touch/`.
- **React Icons**, kamijin_fanta and contributors: icon rendering helpers under MIT, retained as attribution for the original source plugin, which used the Font Awesome 5 `gamepad`, `info-circle` and `sliders-h` icons by Fonticons, Inc., licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); see [Font Awesome](https://fontawesome.com/). The integrated touch page does not use React Icons or Font Awesome: its icons are inline SVG and `react-icons` is not a dependency. The React Icons notice is retained in `licenses/freedom-touch/React-Icons-LICENSE.txt`.
- **Armada**, Armada contributors: [PR #676](https://github.com/armada-os/armada/pull/676) provides the reference for the native X11 overlay, Gamescope integration and InputPlumber touch-event interface. Upstream authors retain copyright in their work. Armada's original scripts are GPL-2.0-or-later, which is compatible with this plugin's GPL-2.0 combined work.
- **InputPlumber**, ShadowBlip contributors: a separately patched system component, based on commit `ea60d873cca17edd1cb655ede26f557108135252`, under GPL-3.0-or-later. It is not bundled with this plugin.
- **Gamescope**, Valve and contributing authors: a separately patched system component based on OpenGamingCollective `3.16.31-ogc1`; it retains its upstream license and copyright notices in `licenses/freedom-touch/Gamescope-LICENSE.txt`.
- **DroidDeck**, Droid-Deck contributors: design reference for multitouch ownership and virtual layouts at commit `8baa16073fa614f02b5e3a629164b582d2eec49a`. Its Android runtime and Mobile Touch compatibility code are not bundled.

The plugin license does not replace third-party licenses. Runtime libraries and build dependencies retain their respective licenses.
