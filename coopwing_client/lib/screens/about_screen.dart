import 'package:flutter/material.dart';

import '../models/app_event.dart';
import '../services/audio_easter_egg_player.dart';
import '../services/mock_backend_client.dart';
import '../services/localization.dart';
import '../widgets/app_log_panel.dart';

class AboutScreen extends StatefulWidget {
  const AboutScreen({
    super.key,
    required this.developerMode,
    required this.events,
  });

  final bool developerMode;
  final List<AppEvent> events;

  @override
  State<AboutScreen> createState() => _AboutScreenState();
}

class _AboutScreenState extends State<AboutScreen> {
  final AudioEasterEggPlayer _audioPlayer = AudioEasterEggPlayer();

  @override
  void dispose() {
    _audioPlayer.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 800),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Container(
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(12),
                    boxShadow: [
                      BoxShadow(
                        color: Colors.black.withValues(alpha: 0.06),
                        blurRadius: 18,
                        offset: const Offset(0, 4),
                      ),
                    ],
                  ),
                  padding: const EdgeInsets.fromLTRB(36, 36, 36, 28),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        loc.get('app_title'),
                        style: Theme.of(context).textTheme.headlineMedium,
                      ),
                      const SizedBox(height: 8),
                      Text(loc.get('app_subtitle')),
                      const SizedBox(height: 8),
                      Text(loc.get('bundle_preview_summary')),
                      const SizedBox(height: 4),
                      Wrap(
                        spacing: 12,
                        runSpacing: 4,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          Text(
                            '${loc.get('version')}: ${MockBackendClient.previewVersion}',
                          ),
                          InkWell(
                            onTap: () => _audioPlayer.playCiallo(),
                            child: Text(
                              'Ciallo～(∠・ω< )⌒☆',
                              style: TextStyle(
                                color: scheme.primary,
                                decoration: TextDecoration.underline,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 24),
                      Card(
                        margin: EdgeInsets.zero,
                        color: scheme.surfaceContainerLow,
                        child: Padding(
                          padding: const EdgeInsets.all(20),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                loc.get('disclaimer_title'),
                                style: const TextStyle(
                                  fontSize: 18,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(height: 12),
                              _DisclaimerLine(text: loc.get('disclaimer_1')),
                              _DisclaimerLine(text: loc.get('disclaimer_2')),
                              _DisclaimerLine(text: loc.get('disclaimer_3')),
                              _DisclaimerLine(text: loc.get('disclaimer_4')),
                              _DisclaimerLine(text: loc.get('disclaimer_5')),
                            ],
                          ),
                        ),
                      ),
                      if (widget.developerMode) ...[
                        const SizedBox(height: 16),
                        AppLogPanel(events: widget.events),
                      ],
                    ],
                  ),
                ),
                const SizedBox(height: 20),
                // AE easter egg button below the card
                Align(
                  alignment: Alignment.centerRight,
                  child: MouseRegion(
                    cursor: SystemMouseCursors.click,
                    child: InkWell(
                      onTap: () {
                        showDialog(
                          context: context,
                          builder: (context) => AlertDialog(
                            title: Text(loc.get('ae_dialog_title')),
                            content: Text(loc.get('ae_dialog_body')),
                            actions: [
                              TextButton(
                                onPressed: () => Navigator.of(context).pop(),
                                child: Text(loc.get('ae_dialog_btn1')),
                              ),
                              TextButton(
                                onPressed: () => Navigator.of(context).pop(),
                                child: Text(loc.get('ae_dialog_btn2')),
                              ),
                            ],
                          ),
                        );
                      },
                      child: Container(
                        width: 38,
                        height: 38,
                        decoration: BoxDecoration(
                          color: const Color(0xFF1D1B26),
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(
                            color: const Color(0xFFD3C2FF),
                            width: 1.5,
                          ),
                          boxShadow: [
                            BoxShadow(
                              color: Colors.black.withValues(alpha: 0.15),
                              blurRadius: 6,
                              offset: const Offset(0, 2),
                            ),
                          ],
                        ),
                        alignment: Alignment.center,
                        child: const Text(
                          'AΞ',
                          style: TextStyle(
                            color: Color(0xFFD3C2FF),
                            fontSize: 15,
                            fontWeight: FontWeight.bold,
                            fontFamily: 'monospace',
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _DisclaimerLine extends StatelessWidget {
  const _DisclaimerLine({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            Icons.check_circle_outline,
            size: 18,
            color: Theme.of(context).colorScheme.primary,
          ),
          const SizedBox(width: 8),
          Expanded(child: Text(text)),
        ],
      ),
    );
  }
}
