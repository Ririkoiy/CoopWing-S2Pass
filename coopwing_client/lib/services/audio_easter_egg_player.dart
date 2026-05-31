import 'package:audioplayers/audioplayers.dart';

class AudioEasterEggPlayer {
  final AudioPlayer _player = AudioPlayer();

  Future<void> playCiallo() async {
    await _player.stop();
    await _player.play(AssetSource('audio/Ciallo.mp4'));
  }

  Future<void> dispose() async {
    await _player.dispose();
  }
}
