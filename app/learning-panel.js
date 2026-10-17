import Gtk from 'gi://Gtk?version=4.0';
import {APP_ID, LearningPanel} from './learning-panel-ui.js';

const application = new Gtk.Application({application_id: APP_ID});
let panel;
application.connect('activate', () => {
    if (panel && !panel.closed) panel.window.present();
    else panel = new LearningPanel(application);
});
application.run(ARGV);
